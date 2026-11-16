from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.domain.bookings import BookingError, check_cancellable
from app.main import create_app
from app.models.models import Block, Booking, Slot, User
from scripts.seed import SEED_USERS, seed

INSTRUCTOR = next(person for person in SEED_USERS if person["role"] == "instructor")
STUDENTS = [person for person in SEED_USERS if person["role"] == "student"]
STUDENT_A, STUDENT_B = STUDENTS

# The published block: 14:00-15:00 Riyadh, four 15 minute slots, in Room 2A.
BLOCK_START = datetime(2026, 11, 3, 11, 0, tzinfo=timezone.utc)
BLOCK_END = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
SLOT_LENGTH_MIN = 15
SLOT_COUNT = 4

WELL_BEFORE = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
EXACTLY_TWO_HOURS_BEFORE = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
ONE_MINUTE_LATER = datetime(2026, 11, 3, 9, 1, tzinfo=timezone.utc)

TOO_LATE = "Too late to cancel: less than 120 minutes before the start"
NOT_ACTIVE = "This booking is not active"


@pytest.fixture
def db_engine(tmp_path):
    """A throwaway SQLite database, seeded with the demo users."""
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = make_engine(url)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        seed(session)
    yield engine
    engine.dispose()


@pytest.fixture
def client(db_engine):
    """A test client whose app uses a frozen clock."""
    with TestClient(create_app(db_engine)) as test_client:
        test_client.app.state.clock = lambda: WELL_BEFORE
        yield test_client


@pytest.fixture
def seeded(db_engine):
    """Insert the block, its four slots, and an active booking by student A.

    Returns the slot ids and the id of A's booking on the first slot.
    """
    with Session(db_engine) as session:
        instructor = session.scalars(
            select(User).where(User.role == "instructor").order_by(User.id)
        ).first()
        block = Block(
            instructor_id=instructor.id,
            start_utc=BLOCK_START,
            end_utc=BLOCK_END,
            slot_length_min=SLOT_LENGTH_MIN,
            location="Room 2A",
            status="open",
        )
        session.add(block)
        session.flush()
        for index in range(SLOT_COUNT):
            start = BLOCK_START + timedelta(minutes=SLOT_LENGTH_MIN * index)
            session.add(
                Slot(
                    block_id=block.id,
                    start_utc=start,
                    end_utc=start + timedelta(minutes=SLOT_LENGTH_MIN),
                )
            )
        session.commit()
        slot_ids = session.scalars(select(Slot.id).order_by(Slot.start_utc)).all()

        booking = Booking(
            slot_id=slot_ids[0],
            student_id=user_id(db_engine, STUDENT_A),
            topic="HW1 question",
            status="active",
        )
        session.add(booking)
        session.commit()

        return {"slot_ids": slot_ids, "booking_id": booking.id}


def login(client, person):
    return client.post(
        "/login",
        data={"email": person["email"], "password": person["password"]},
        follow_redirects=False,
    )


def user_id(db_engine, person):
    with Session(db_engine) as session:
        return session.scalar(select(User.id).where(User.email == person["email"]))


def booking_status(db_engine, booking_id):
    with Session(db_engine) as session:
        return session.scalar(select(Booking.status).where(Booking.id == booking_id))


def count_bookings(db_engine):
    with Session(db_engine) as session:
        return session.scalar(select(func.count()).select_from(Booking))


def count_active_bookings(db_engine):
    with Session(db_engine) as session:
        return session.scalar(
            select(func.count()).select_from(Booking).where(Booking.status == "active")
        )


def cancel(client, booking_id):
    return client.post(f"/student/bookings/{booking_id}/cancel", follow_redirects=False)


def book(client, slot_id, topic=""):
    return client.post(
        "/student/bookings",
        data={"slot_id": str(slot_id), "topic": topic},
        follow_redirects=False,
    )


# --- domain rules -------------------------------------------------------------------


def test_120_minutes_before_the_start_is_still_cancellable():
    assert check_cancellable(BLOCK_START, EXACTLY_TWO_HOURS_BEFORE) is None


def test_119_minutes_before_the_start_is_too_late():
    with pytest.raises(BookingError, match=TOO_LATE):
        check_cancellable(BLOCK_START, ONE_MINUTE_LATER)


def test_121_minutes_before_the_start_is_still_cancellable():
    check_cancellable(BLOCK_START, EXACTLY_TWO_HOURS_BEFORE - timedelta(minutes=1))


def test_a_slot_that_already_started_cannot_be_cancelled():
    with pytest.raises(BookingError, match=TOO_LATE):
        check_cancellable(BLOCK_START, BLOCK_START)


@pytest.mark.parametrize(
    "slot_start,now",
    [
        (datetime(2026, 11, 3, 11), EXACTLY_TWO_HOURS_BEFORE),
        (BLOCK_START, datetime(2026, 11, 3, 9)),
    ],
)
def test_naive_datetimes_are_rejected(slot_start, now):
    with pytest.raises(ValueError):
        check_cancellable(slot_start, now)


# --- cancelling through the page ----------------------------------------------------


def test_sc11_exactly_two_hours_before_frees_the_slot(client, db_engine, seeded):
    client.app.state.clock = lambda: EXACTLY_TWO_HOURS_BEFORE
    login(client, STUDENT_A)
    first_slot = seeded["slot_ids"][0]

    response = cancel(client, seeded["booking_id"])

    assert response.status_code == 303
    assert response.headers["location"] == "/student"
    assert booking_status(db_engine, seeded["booking_id"]) == "cancelled_by_student"
    # The row is kept, only its status changed.
    assert count_bookings(db_engine) == 1
    assert count_active_bookings(db_engine) == 0

    page = client.get("/student")
    assert f'name="slot_id" value="{first_slot}"' in page.text

    login(client, STUDENT_B)
    assert book(client, first_slot, "mine now").status_code == 303
    assert count_active_bookings(db_engine) == 1


def test_sc12_one_minute_inside_the_cutoff_is_too_late(client, db_engine, seeded):
    client.app.state.clock = lambda: ONE_MINUTE_LATER
    login(client, STUDENT_A)

    response = cancel(client, seeded["booking_id"])

    assert response.status_code == 400
    assert TOO_LATE in response.text
    assert booking_status(db_engine, seeded["booking_id"]) == "active"


def test_another_student_cannot_cancel_the_booking(client, db_engine, seeded):
    login(client, STUDENT_B)

    response = cancel(client, seeded["booking_id"])

    assert response.status_code == 404
    assert booking_status(db_engine, seeded["booking_id"]) == "active"


def test_an_unknown_booking_id_is_a_404(client, db_engine, seeded):
    login(client, STUDENT_A)

    assert cancel(client, 99999).status_code == 404


def test_an_instructor_cannot_cancel_a_booking(client, db_engine, seeded):
    login(client, INSTRUCTOR)

    response = cancel(client, seeded["booking_id"])

    assert response.status_code == 403
    assert booking_status(db_engine, seeded["booking_id"]) == "active"


def test_cancelling_twice_reports_the_booking_is_not_active(client, db_engine, seeded):
    login(client, STUDENT_A)

    assert cancel(client, seeded["booking_id"]).status_code == 303

    response = cancel(client, seeded["booking_id"])

    assert response.status_code == 400
    assert NOT_ACTIVE in response.text
    assert booking_status(db_engine, seeded["booking_id"]) == "cancelled_by_student"
