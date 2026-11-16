from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.deps import get_db, require_role
from app.domain.blocks import BlockError, plan_block
from app.domain.bookings import (
    BookingError,
    check_cancellable,
    check_instructor_limit,
    check_no_overlap,
    check_slot_bookable,
    clean_topic,
)
from app.domain.constants import MAX_AHEAD_DAYS, MIN_LEAD_MIN
from app.models.models import Block, Booking, Slot, User
from app.timeutil import as_utc, riyadh_to_utc, to_riyadh
from app.web.templating import templates

router = APIRouter()

LOCATION_MAX_CHARS = 120
LOCATION_ERROR = "location is required (max 120 characters)"
FORMAT_ERROR = "invalid date or time"
SLOT_TAKEN = "This slot was just booked by someone else"
NOT_ACTIVE = "This booking is not active"


def block_rows(db, user):
    """This instructor's blocks, newest start first, with Riyadh times for the page."""
    blocks = db.scalars(
        select(Block).where(Block.instructor_id == user.id).order_by(Block.start_utc.desc())
    ).all()

    rows = []
    for block in blocks:
        slot_starts = db.scalars(
            select(Slot.start_utc).where(Slot.block_id == block.id).order_by(Slot.start_utc)
        ).all()
        start = to_riyadh(block.start_utc)
        rows.append(
            {
                "date": start.strftime("%Y-%m-%d"),
                "start": start.strftime("%H:%M"),
                "end": to_riyadh(block.end_utc).strftime("%H:%M"),
                "location": block.location,
                "slots": [to_riyadh(slot_start).strftime("%H:%M") for slot_start in slot_starts],
            }
        )
    return rows


def instructor_page(request, user, db, error=None, status_code=200):
    """Render the instructor home page, optionally with an error message."""
    return templates.TemplateResponse(
        request,
        "instructor_home.html",
        {"user": user, "blocks": block_rows(db, user), "error": error},
        status_code=status_code,
    )


def open_slot_groups(db, now):
    """Bookable slots inside the booking window, grouped by instructor name."""
    earliest = now + timedelta(minutes=MIN_LEAD_MIN)
    latest = now + timedelta(days=MAX_AHEAD_DAYS)
    has_active_booking = (
        select(Booking.id)
        .where(Booking.slot_id == Slot.id, Booking.status == "active")
        .exists()
    )
    rows = db.execute(
        select(Slot, Block, User.name)
        .join(Block, Slot.block_id == Block.id)
        .join(User, Block.instructor_id == User.id)
        .where(
            Slot.start_utc >= earliest,
            Slot.start_utc <= latest,
            Block.status != "cancelled",
            ~has_active_booking,
        )
        .order_by(Slot.start_utc)
    ).all()

    groups = {}
    for slot, block, instructor_name in rows:
        start = to_riyadh(slot.start_utc)
        groups.setdefault(instructor_name, []).append(
            {
                "id": slot.id,
                "date": start.strftime("%Y-%m-%d"),
                "start": start.strftime("%H:%M"),
                "end": to_riyadh(slot.end_utc).strftime("%H:%M"),
                "location": block.location,
            }
        )
    return [{"instructor": name, "slots": slots} for name, slots in groups.items()]


def student_booking_rows(db, user):
    """This student's active bookings, earliest slot first."""
    rows = db.execute(
        select(Booking, Slot, Block, User.name)
        .join(Slot, Booking.slot_id == Slot.id)
        .join(Block, Slot.block_id == Block.id)
        .join(User, Block.instructor_id == User.id)
        .where(Booking.student_id == user.id, Booking.status == "active")
        .order_by(Slot.start_utc)
    ).all()

    bookings = []
    for booking, slot, block, instructor_name in rows:
        start = to_riyadh(slot.start_utc)
        bookings.append(
            {
                "id": booking.id,
                "date": start.strftime("%Y-%m-%d"),
                "start": start.strftime("%H:%M"),
                "end": to_riyadh(slot.end_utc).strftime("%H:%M"),
                "instructor": instructor_name,
                "location": block.location,
                "topic": booking.topic,
            }
        )
    return bookings


def student_page(request, user, db, error=None, status_code=200):
    """Render the student home page, optionally with an error message."""
    return templates.TemplateResponse(
        request,
        "student_home.html",
        {
            "user": user,
            "open_slots": open_slot_groups(db, request.app.state.clock()),
            "bookings": student_booking_rows(db, user),
            "error": error,
        },
        status_code=status_code,
    )


@router.get("/student")
def student_home(
    request: Request,
    user: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
):
    return student_page(request, user, db)


@router.post("/student/bookings")
def create_booking(
    request: Request,
    user: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
    slot_id: str = Form(""),
    topic: str = Form(""),
):
    # The role check above already ran, so an instructor never reaches these checks.
    try:
        slot_pk = int(slot_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=404)

    slot = db.get(Slot, slot_pk)
    if slot is None:
        raise HTTPException(status_code=404)
    block = db.get(Block, slot.block_id)
    if block is None:
        raise HTTPException(status_code=404)

    now = request.app.state.clock()
    try:
        topic = clean_topic(topic)
        check_slot_bookable(as_utc(slot.start_utc), block.status, now)
    except BookingError as error:
        return student_page(request, user, db, str(error), status_code=400)

    already_booked = db.scalar(
        select(Booking.id).where(Booking.slot_id == slot.id, Booking.status == "active")
    )
    if already_booked is not None:
        return student_page(request, user, db, SLOT_TAKEN, status_code=409)

    active_with_instructor = db.scalar(
        select(func.count())
        .select_from(Booking)
        .join(Slot, Booking.slot_id == Slot.id)
        .join(Block, Slot.block_id == Block.id)
        .where(
            Booking.student_id == user.id,
            Booking.status == "active",
            Slot.start_utc > now,
            Block.instructor_id == block.instructor_id,
        )
    )
    try:
        check_instructor_limit(active_with_instructor)
    except BookingError as error:
        return student_page(request, user, db, str(error), status_code=400)

    existing = db.execute(
        select(Slot.start_utc, Slot.end_utc, User.name)
        .join(Booking, Booking.slot_id == Slot.id)
        .join(Block, Slot.block_id == Block.id)
        .join(User, Block.instructor_id == User.id)
        .where(
            Booking.student_id == user.id,
            Booking.status == "active",
            Slot.start_utc > now,
        )
        .order_by(Slot.start_utc)
    ).all()

    taken = []
    for old_start, old_end, instructor_name in existing:
        local_start = to_riyadh(old_start)
        local_end = to_riyadh(old_end)
        taken.append(
            (
                as_utc(old_start),
                as_utc(old_end),
                f"with {instructor_name} on {local_start.strftime('%Y-%m-%d')} "
                f"{local_start.strftime('%H:%M')}-{local_end.strftime('%H:%M')}",
            )
        )

    try:
        check_no_overlap(as_utc(slot.start_utc), as_utc(slot.end_utc), taken)
    except BookingError as error:
        return student_page(request, user, db, str(error), status_code=400)

    db.add(Booking(slot_id=slot.id, student_id=user.id, topic=topic, status="active"))
    try:
        db.commit()
    except IntegrityError:
        # Another student committed first; the database refused the second row.
        db.rollback()
        return student_page(request, user, db, SLOT_TAKEN, status_code=409)

    return RedirectResponse("/student", status_code=303)


@router.post("/student/bookings/{booking_id}/cancel")
def cancel_booking(
    booking_id: int,
    request: Request,
    user: User = Depends(require_role("student")),
    db: Session = Depends(get_db),
):
    # A missing booking and someone else's booking get the same answer.
    booking = db.get(Booking, booking_id)
    if booking is None or booking.student_id != user.id:
        raise HTTPException(status_code=404)

    if booking.status != "active":
        return student_page(request, user, db, NOT_ACTIVE, status_code=400)

    slot = db.get(Slot, booking.slot_id)
    if slot is None:
        raise HTTPException(status_code=404)

    try:
        check_cancellable(as_utc(slot.start_utc), request.app.state.clock())
    except BookingError as error:
        return student_page(request, user, db, str(error), status_code=400)

    # The row stays: the slot is freed by the status alone.
    booking.status = "cancelled_by_student"
    db.commit()

    return RedirectResponse("/student", status_code=303)


@router.get("/instructor")
def instructor_home(
    request: Request,
    user: User = Depends(require_role("instructor")),
    db: Session = Depends(get_db),
):
    return instructor_page(request, user, db)


@router.post("/instructor/blocks")
def create_block(
    request: Request,
    user: User = Depends(require_role("instructor")),
    db: Session = Depends(get_db),
    date: str = Form(""),
    start_time: str = Form(""),
    end_time: str = Form(""),
    slot_length: str = Form(""),
    location: str = Form(""),
):
    # The role check above already ran, so a student never reaches these form checks.
    location = location.strip()
    if not location or len(location) > LOCATION_MAX_CHARS:
        return instructor_page(request, user, db, LOCATION_ERROR, status_code=400)

    try:
        start_utc = riyadh_to_utc(date, start_time)
        end_utc = riyadh_to_utc(date, end_time)
        slot_length_min = int(slot_length)
    except ValueError:
        return instructor_page(request, user, db, FORMAT_ERROR, status_code=400)

    existing = db.execute(
        select(Block.start_utc, Block.end_utc).where(
            Block.instructor_id == user.id,
            Block.status != "cancelled",
        )
    ).all()

    try:
        slots = plan_block(
            start_utc,
            end_utc,
            slot_length_min,
            request.app.state.clock(),
            [(as_utc(old_start), as_utc(old_end)) for old_start, old_end in existing],
        )
    except BlockError as error:
        return instructor_page(request, user, db, str(error), status_code=400)

    block = Block(
        instructor_id=user.id,
        start_utc=start_utc,
        end_utc=end_utc,
        slot_length_min=slot_length_min,
        location=location,
        status="open",
    )
    db.add(block)
    db.flush()
    for slot_start, slot_end in slots:
        db.add(Slot(block_id=block.id, start_utc=slot_start, end_utc=slot_end))
    db.commit()

    return RedirectResponse("/instructor", status_code=303)
