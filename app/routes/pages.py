from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_db, require_role
from app.domain.blocks import BlockError, plan_block
from app.models.models import Block, Slot, User
from app.timeutil import as_utc, riyadh_to_utc, to_riyadh
from app.web.templating import templates

router = APIRouter()

LOCATION_MAX_CHARS = 120
LOCATION_ERROR = "location is required (max 120 characters)"
FORMAT_ERROR = "invalid date or time"


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


@router.get("/student")
def student_home(request: Request, user: User = Depends(require_role("student"))):
    return templates.TemplateResponse(request, "student_home.html", {"user": user})


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
