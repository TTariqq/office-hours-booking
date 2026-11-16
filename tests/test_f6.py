from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.domain.bookings import BookingError, check_slot_bookable
from app.main import create_app
from app.models.models import Block, Booking, Slot, User
from scripts.seed import SEED_USERS, seed

INSTRUCTOR = next(person for person in SEED_USERS if person["role"] == "instructor")
STUDENTS = [person for person in SEED_USERS if person["role"] == "student"]
STUDENT_A, STUDENT_B = STUDENTS

# The published block: 14:00-15:00 Riyadh, four 15 minute slots, in Room 2A.
SLOT_START = datetime(2026, 11, 3, 11, 0, tzinfo=timezone.utc)
BLOCK_END = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
SLOT_LENGTH_MIN = 15
SLOT_COUNT = 4

# Clocks around the 60 minute lead.
ONE_MINUTE_OUTSIDE_THE_LEAD = datetime(2026, 11, 3, 9, 59, tzinfo=timezone.utc)
EXACTLY_ONE_HOUR_BEFORE = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
ONE_MINUTE_INSIDE_THE_LEAD = datetime(2026, 11, 3, 10, 1, tzinfo=timezone.utc)

# Clocks around the 14 day reach.
FOURTEEN_DAYS_BEFORE = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
F1_START = datetime(2026, 11, 15, 8, 0, tzinfo=timezone.utc)
F2_START = datetime(2026, 11, 15, 8, 1, tzinfo=timezone.utc)

TOO_SOON = "Too soon to book: the slot starts in less than 60 minutes"
TOO_FAR = "Too far ahead: you can book at most 14 days ahead"
SLOT_GONE = "This slot is no longer available"


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
        test_client.app.state.clock = lambda: FOURTEEN_DAYS_BEFORE
        yield test_client


def add_block(db_engine, start_utc, slot_count):
    """Insert one open block with its slots for the first seeded instructor.

    Returns the new slot ids, earliest first.
    """
    with Session(db_engine) as session:
        instructor = session.scalars(
            select(User).where(User.role == "instructor").order_by(User.id)
        ).first()
        block = Block(
            instructor_id=instructor.id,
            start_utc=start_utc,
            end_utc=start_utc + timedelta(minutes=SLOT_LENGTH_MIN * slot_count),
            slot_length_min=SLOT_LENGTH_MIN,
            location="Room 2A",
            status="open",
        )
        session.add(block)
        session.flush()
        slot_ids = []
        for index in range(slot_count):
            start = start_utc + timedelta(minutes=SLOT_LENGTH_MIN * index)
            slot = Slot(
                block_id=block.id,
                start_utc=start,
                end_utc=start + timedelta(minutes=SLOT_LENGTH_MIN),
            )
            session.add(slot)
            session.flush()
            slot_ids.append(slot.id)
        session.commit()
        return slot_ids


@pytest.fixture
def slot_s(db_engine):
    """The 14:00 Riyadh block with four slots. Returns the slot ids, earliest first."""
    return add_block(db_engine, SLOT_START, SLOT_COUNT)


@pytest.fixture
def far_slots(db_engine):
    """Two separate blocks on 2026-11-15, one slot each: F1 at 8:00, F2 at 8:01."""
    f1_slot = add_block(db_engine, F1_START, 1)[0]
    f2_slot = add_block(db_engine, F2_START, 1)[0]
    return {"F1": f1_slot, "F2": f2_slot}


def login(client, person):
    return client.post(
        "/login",
        data={"email": person["email"], "password": person["password"]},
        follow_redirects=False,
    )


def count_bookings(db_engine):
    with Session(db_engine) as session:
        return session.scalar(select(func.count()).select_from(Booking))


def book(client, slot_id, topic=""):
    return client.post(
        "/student/bookings",
        data={"slot_id": str(slot_id), "topic": topic},
        follow_redirects=False,
    )


# --- domain rules -------------------------------------------------------------------


def test_sixty_minutes_of_lead_is_allowed():
    check_slot_bookable(SLOT_START, "open", SLOT_START - timedelta(minutes=60))


def test_fifty_nine_minutes_of_lead_is_too_soon():
    with pytest.raises(BookingError, match=TOO_SOON):
        check_slot_bookable(SLOT_START, "open", SLOT_START - timedelta(minutes=59))


def test_sixty_one_minutes_of_lead_is_allowed():
    check_slot_bookable(SLOT_START, "open", SLOT_START - timedelta(minutes=61))


def test_exactly_fourteen_days_ahead_is_allowed():
    check_slot_bookable(SLOT_START, "open", SLOT_START - timedelta(days=14))


def test_fourteen_days_and_one_minute_ahead_is_too_far():
    with pytest.raises(BookingError, match=TOO_FAR):
        check_slot_bookable(SLOT_START, "open", SLOT_START - timedelta(days=14, minutes=1))


def test_a_slot_that_already_started_is_gone():
    with pytest.raises(BookingError, match=SLOT_GONE):
        check_slot_bookable(SLOT_START, "open", SLOT_START)


# --- the booking window on the page -------------------------------------------------


def test_sc04_a_slot_one_minute_outside_the_lead_is_listed_and_bookable(
    client, db_engine, slot_s
):
    client.app.state.clock = lambda: ONE_MINUTE_OUTSIDE_THE_LEAD
    login(client, STUDENT_A)
    first_slot = slot_s[0]

    page = client.get("/student")
    assert f'name="slot_id" value="{first_slot}"' in page.text

    response = book(client, first_slot, "HW2 question")

    assert response.status_code == 303
    assert response.headers["location"] == "/student"
    assert count_bookings(db_engine) == 1


def test_exactly_sixty_minutes_of_lead_books(client, db_engine, slot_s):
    client.app.state.clock = lambda: EXACTLY_ONE_HOUR_BEFORE
    login(client, STUDENT_A)

    assert book(client, slot_s[0], "just in time").status_code == 303
    assert count_bookings(db_engine) == 1


def test_sc05_one_minute_inside_the_lead_is_too_soon(client, db_engine, slot_s):
    client.app.state.clock = lambda: ONE_MINUTE_INSIDE_THE_LEAD
    login(client, STUDENT_A)
    first_slot = slot_s[0]

    page = client.get("/student")
    assert f'name="slot_id" value="{first_slot}"' not in page.text

    response = book(client, first_slot, "too late")

    assert response.status_code == 400
    assert TOO_SOON in response.text
    assert count_bookings(db_engine) == 0


def test_sc06_exactly_fourteen_days_ahead_books_but_one_minute_more_does_not(
    client, db_engine, far_slots
):
    client.app.state.clock = lambda: FOURTEEN_DAYS_BEFORE
    login(client, STUDENT_A)

    page = client.get("/student")
    assert f'name="slot_id" value="{far_slots["F1"]}"' in page.text
    assert f'name="slot_id" value="{far_slots["F2"]}"' not in page.text

    assert book(client, far_slots["F1"], "two weeks out").status_code == 303

    response = book(client, far_slots["F2"], "one minute too far")

    assert response.status_code == 400
    assert TOO_FAR in response.text
    assert count_bookings(db_engine) == 1
