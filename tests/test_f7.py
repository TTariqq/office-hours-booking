from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.domain.bookings import BookingError, check_instructor_limit
from app.main import create_app
from app.models.models import Block, Booking, Slot, User
from scripts.seed import SEED_USERS, seed

SARA, OMAR = [person for person in SEED_USERS if person["role"] == "instructor"]
STUDENT_A, STUDENT_B = [person for person in SEED_USERS if person["role"] == "student"]

# The clock every test runs on.
NOW = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

SLOT_LENGTH_MIN = 15

# Dr. Sara: four slots, 07:00-08:00 UTC on 2026-11-03.
SARA_START = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
SARA_SLOT_COUNT = 4
SARA_LOCATION = "Room 2A"

# Dr. Omar: four slots, 09:00-10:00 UTC on 2026-11-03.
OMAR_START = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
OMAR_SLOT_COUNT = 4
OMAR_LOCATION = "Room 3B"

# Dr. Sara: two slots already in the past on 2026-10-30.
PAST_START = datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc)
PAST_SLOT_COUNT = 2

LIMIT_MESSAGE = "You already have 2 active bookings with this instructor"


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
        test_client.app.state.clock = lambda: NOW
        yield test_client


def user_id(db_engine, person):
    with Session(db_engine) as session:
        return session.scalar(select(User.id).where(User.email == person["email"]))


def add_block(db_engine, instructor, start_utc, slot_count, location):
    """Insert one open block with its slots. Returns the new slot ids, earliest first."""
    with Session(db_engine) as session:
        instructor_id = session.scalar(select(User.id).where(User.email == instructor["email"]))
        block = Block(
            instructor_id=instructor_id,
            start_utc=start_utc,
            end_utc=start_utc + timedelta(minutes=SLOT_LENGTH_MIN * slot_count),
            slot_length_min=SLOT_LENGTH_MIN,
            location=location,
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
def slots(db_engine):
    """The three published blocks. Each value is the block's slot ids, earliest first."""
    return {
        "sara": add_block(db_engine, SARA, SARA_START, SARA_SLOT_COUNT, SARA_LOCATION),
        "omar": add_block(db_engine, OMAR, OMAR_START, OMAR_SLOT_COUNT, OMAR_LOCATION),
        "past": add_block(db_engine, SARA, PAST_START, PAST_SLOT_COUNT, SARA_LOCATION),
    }


def add_booking(db_engine, student, slot_id, status="active"):
    """Insert a booking directly, bypassing the routes."""
    with Session(db_engine) as session:
        student_id = session.scalar(select(User.id).where(User.email == student["email"]))
        session.add(
            Booking(slot_id=slot_id, student_id=student_id, topic="pre-existing", status=status)
        )
        session.commit()


def login(client, person):
    return client.post(
        "/login",
        data={"email": person["email"], "password": person["password"]},
        follow_redirects=False,
    )


def book(client, slot_id, topic=""):
    return client.post(
        "/student/bookings",
        data={"slot_id": str(slot_id), "topic": topic},
        follow_redirects=False,
    )


def active_bookings(db_engine, student):
    """How many active bookings this student holds."""
    with Session(db_engine) as session:
        student_id = session.scalar(select(User.id).where(User.email == student["email"]))
        return session.scalar(
            select(func.count())
            .select_from(Booking)
            .where(Booking.student_id == student_id, Booking.status == "active")
        )


def total_bookings(db_engine):
    with Session(db_engine) as session:
        return session.scalar(select(func.count()).select_from(Booking))


# --- domain rule --------------------------------------------------------------------


def test_zero_active_bookings_pass():
    check_instructor_limit(0)


def test_one_active_booking_passes():
    check_instructor_limit(1)


def test_two_active_bookings_are_refused():
    with pytest.raises(BookingError) as raised:
        check_instructor_limit(2)
    assert str(raised.value) == LIMIT_MESSAGE


def test_three_active_bookings_are_refused():
    with pytest.raises(BookingError) as raised:
        check_instructor_limit(3)
    assert str(raised.value) == LIMIT_MESSAGE


# --- the limit at the booking form --------------------------------------------------


def test_boundary_a_second_booking_with_the_same_instructor_books(client, db_engine, slots):
    """One active booking is still below the limit, so a second one goes through."""
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["sara"][0])
    assert active_bookings(db_engine, STUDENT_A) == 1

    response = book(client, slots["sara"][1], "second with Sara")

    assert response.status_code == 303
    assert response.headers["location"] == "/student"
    assert active_bookings(db_engine, STUDENT_A) == 2


def test_sc07_a_third_booking_with_the_same_instructor_is_refused(
    client, db_engine, slots
):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["sara"][0])
    add_booking(db_engine, STUDENT_A, slots["sara"][1])

    response = book(client, slots["sara"][2], "third with Sara")

    assert response.status_code == 400
    assert LIMIT_MESSAGE in response.text
    assert active_bookings(db_engine, STUDENT_A) == 2

    # Another instructor is a different count, so that booking still works.
    response = book(client, slots["omar"][0], "first with Omar")

    assert response.status_code == 303
    assert active_bookings(db_engine, STUDENT_A) == 3


def test_a_cancelled_booking_does_not_count(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["sara"][0], status="active")
    add_booking(db_engine, STUDENT_A, slots["sara"][1], status="cancelled_by_student")

    response = book(client, slots["sara"][2], "Sara again")

    assert response.status_code == 303
    assert active_bookings(db_engine, STUDENT_A) == 2


def test_a_past_booking_does_not_count(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["past"][0], status="active")
    add_booking(db_engine, STUDENT_A, slots["sara"][0], status="active")

    response = book(client, slots["sara"][1], "Sara again")

    assert response.status_code == 303
    assert active_bookings(db_engine, STUDENT_A) == 3


def test_another_students_bookings_do_not_count(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_B, slots["sara"][0])
    add_booking(db_engine, STUDENT_B, slots["sara"][1])

    response = book(client, slots["sara"][2], "mine")

    assert response.status_code == 303
    assert active_bookings(db_engine, STUDENT_A) == 1
    assert total_bookings(db_engine) == 3
