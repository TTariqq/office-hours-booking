from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.domain.bookings import BookingError, check_no_overlap
from app.main import create_app
from app.models.models import Block, Booking, Slot, User
from scripts.seed import SEED_USERS, seed

SARA, OMAR = [person for person in SEED_USERS if person["role"] == "instructor"]
STUDENT_A, STUDENT_B = [person for person in SEED_USERS if person["role"] == "student"]

# The clock every test runs on.
NOW = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

# Dr. Sara, first block: four 15-minute slots, 07:00-08:00 UTC on 2026-11-03.
SARA_START = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
SARA_SLOT_COUNT = 4
SARA_LOCATION = "Room 2A"

# Dr. Omar, short block: two 15-minute slots, 07:00-07:30 UTC on 2026-11-03.
OMAR15_START = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
OMAR15_SLOT_COUNT = 2
OMAR15_LOCATION = "Room 3B"

# Dr. Omar, long block: three 20-minute slots, 07:00-08:00 UTC on 2026-11-04.
OMAR20_START = datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc)
OMAR20_SLOT_COUNT = 3
OMAR20_LENGTH_MIN = 20
OMAR20_LOCATION = "Room 3B"

# Dr. Sara, second block: four 15-minute slots, 07:00-08:00 UTC on 2026-11-04.
SARA4_START = datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc)
SARA4_SLOT_COUNT = 4
SARA4_LOCATION = "Room 2A"

SLOT_LENGTH_MIN = 15

OVERLAP_WITH_OMAR_20 = "This overlaps your booking with Dr. Omar Khalid on 2026-11-04 10:00-10:20"


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


def add_block(db_engine, instructor, start_utc, slot_count, location, slot_length_min=SLOT_LENGTH_MIN):
    """Insert one open block with its slots. Returns the new slot ids, earliest first."""
    with Session(db_engine) as session:
        instructor_id = session.scalar(select(User.id).where(User.email == instructor["email"]))
        block = Block(
            instructor_id=instructor_id,
            start_utc=start_utc,
            end_utc=start_utc + timedelta(minutes=slot_length_min * slot_count),
            slot_length_min=slot_length_min,
            location=location,
            status="open",
        )
        session.add(block)
        session.flush()
        slot_ids = []
        for index in range(slot_count):
            start = start_utc + timedelta(minutes=slot_length_min * index)
            slot = Slot(
                block_id=block.id,
                start_utc=start,
                end_utc=start + timedelta(minutes=slot_length_min),
            )
            session.add(slot)
            session.flush()
            slot_ids.append(slot.id)
        session.commit()
        return slot_ids


@pytest.fixture
def slots(db_engine):
    """The four published blocks. Each value is the block's slot ids, earliest first."""
    return {
        "sara": add_block(db_engine, SARA, SARA_START, SARA_SLOT_COUNT, SARA_LOCATION),
        "omar15": add_block(db_engine, OMAR, OMAR15_START, OMAR15_SLOT_COUNT, OMAR15_LOCATION),
        "omar20": add_block(
            db_engine,
            OMAR,
            OMAR20_START,
            OMAR20_SLOT_COUNT,
            OMAR20_LOCATION,
            slot_length_min=OMAR20_LENGTH_MIN,
        ),
        "sara4": add_block(db_engine, SARA, SARA4_START, SARA4_SLOT_COUNT, SARA4_LOCATION),
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


# --- domain rule --------------------------------------------------------------------


def test_a_range_that_ends_where_another_starts_passes():
    existing = [(datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
                 datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc),
                 "later")]
    check_no_overlap(
        datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
        existing,
    )


def test_a_range_that_starts_where_another_ends_passes():
    existing = [(datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
                 datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
                 "earlier")]
    check_no_overlap(
        datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc),
        existing,
    )


def test_a_partial_overlap_is_refused():
    existing = [(datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
                 datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
                 "with Dr. Sara Ahmed on 2026-11-03 10:00-11:00")]
    with pytest.raises(BookingError) as raised:
        check_no_overlap(
            datetime(2026, 11, 3, 7, 30, tzinfo=timezone.utc),
            datetime(2026, 11, 3, 8, 30, tzinfo=timezone.utc),
            existing,
        )
    assert "with Dr. Sara Ahmed on 2026-11-03 10:00-11:00" in str(raised.value)


def test_the_same_range_is_refused():
    existing = [(datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
                 datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
                 "same time")]
    with pytest.raises(BookingError) as raised:
        check_no_overlap(
            datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
            existing,
        )
    assert "same time" in str(raised.value)


def test_a_range_inside_an_existing_one_is_refused():
    existing = [(datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
                 datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
                 "the big one")]
    with pytest.raises(BookingError) as raised:
        check_no_overlap(
            datetime(2026, 11, 3, 7, 15, tzinfo=timezone.utc),
            datetime(2026, 11, 3, 7, 30, tzinfo=timezone.utc),
            existing,
        )
    assert "the big one" in str(raised.value)


def test_an_empty_list_passes():
    check_no_overlap(
        datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
        [],
    )


def test_a_naive_new_start_is_refused():
    with pytest.raises(ValueError):
        check_no_overlap(
            datetime(2026, 11, 3, 7, 0),
            datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc),
            [],
        )


def test_a_naive_new_end_is_refused():
    with pytest.raises(ValueError):
        check_no_overlap(
            datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 3, 8, 0),
            [],
        )


def test_a_naive_existing_range_is_refused():
    existing = [(datetime(2026, 11, 3, 7, 0), datetime(2026, 11, 3, 8, 0), "naive")]
    with pytest.raises(ValueError):
        check_no_overlap(
            datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc),
            existing,
        )


# --- the rule at the booking form ---------------------------------------------------


def test_sc08_touching_bookings_are_allowed(client, db_engine, slots):
    """A booking that starts exactly when another ends is not an overlap."""
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["omar15"][0])
    assert active_bookings(db_engine, STUDENT_A) == 1

    response = book(client, slots["sara"][1], "right after Omar")

    assert response.status_code == 303
    assert response.headers["location"] == "/student"
    assert active_bookings(db_engine, STUDENT_A) == 2


def test_sc09_an_overlapping_booking_with_another_instructor_is_refused(
    client, db_engine, slots
):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["omar20"][0])

    response = book(client, slots["sara4"][1], "overlapping")

    assert response.status_code == 400
    assert OVERLAP_WITH_OMAR_20 in response.text
    assert active_bookings(db_engine, STUDENT_A) == 1


def test_the_same_exact_time_with_another_instructor_is_refused(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["omar15"][0])

    response = book(client, slots["sara"][0], "same time")

    assert response.status_code == 400
    assert "This overlaps your booking with Dr. Omar Khalid" in response.text
    assert active_bookings(db_engine, STUDENT_A) == 1


def test_a_cancelled_booking_does_not_clash(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["omar20"][0], status="cancelled_by_student")

    response = book(client, slots["sara4"][1], "mine now")

    assert response.status_code == 303
    assert active_bookings(db_engine, STUDENT_A) == 1


def test_another_students_overlapping_booking_does_not_clash(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_B, slots["omar20"][0])

    response = book(client, slots["sara4"][1], "mine now")

    assert response.status_code == 303
    assert active_bookings(db_engine, STUDENT_A) == 1
    assert active_bookings(db_engine, STUDENT_B) == 1
