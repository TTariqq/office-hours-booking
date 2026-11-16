from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.main import create_app
from app.models.models import Block, Booking, Slot, User
from scripts.seed import SEED_USERS, seed

SARA, OMAR = [person for person in SEED_USERS if person["role"] == "instructor"]
STUDENT_A, STUDENT_B = [person for person in SEED_USERS if person["role"] == "student"]

# The clock every test runs on.
NOW = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

SLOT_LENGTH_MIN = 15

# Dr. Sara: four slots on 2026-11-03, 07:00-08:00 UTC. 10:00-11:00 in Riyadh.
SARA_START = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
SARA_LOCATION = "Room 2A"

# Dr. Sara: a cancelled block on 2026-11-04, two slots. 10:00-10:30 in Riyadh.
CANCELLED_START = datetime(2026, 11, 4, 7, 0, tzinfo=timezone.utc)
CANCELLED_LOCATION = "Room 6C"

# Dr. Sara: a block that ended exactly at now, and one that is still running.
ENDED_START = datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)
ENDED_LOCATION = "Room 9Z"
RUNNING_START = datetime(2026, 11, 1, 7, 45, tzinfo=timezone.utc)
RUNNING_LOCATION = "Room 7R"

# Dr. Omar: two slots on 2026-11-03, 09:00-09:30 UTC.
OMAR_START = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
OMAR_LOCATION = "Room 5B"

SLOT_COUNT = 4
TWO_SLOTS = 2


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


def add_block(db_engine, instructor, start_utc, slot_count, location, status="open"):
    """Insert one block with its slots. Returns the block id and its slot ids, earliest first."""
    with Session(db_engine) as session:
        instructor_id = session.scalar(select(User.id).where(User.email == instructor["email"]))
        block = Block(
            instructor_id=instructor_id,
            start_utc=start_utc,
            end_utc=start_utc + timedelta(minutes=SLOT_LENGTH_MIN * slot_count),
            slot_length_min=SLOT_LENGTH_MIN,
            location=location,
            status=status,
        )
        session.add(block)
        session.flush()
        block_id = block.id
        slot_ids = []
        for index in range(slot_count):
            start = start_utc + timedelta(minutes=SLOT_LENGTH_MIN * index)
            slot = Slot(
                block_id=block_id,
                start_utc=start,
                end_utc=start + timedelta(minutes=SLOT_LENGTH_MIN),
            )
            session.add(slot)
            session.flush()
            slot_ids.append(slot.id)
        session.commit()
        return block_id, slot_ids


def add_booking(db_engine, student, slot_id, topic, status="active"):
    """Insert a booking directly, bypassing the routes. Returns its id."""
    with Session(db_engine) as session:
        student_id = session.scalar(select(User.id).where(User.email == student["email"]))
        booking = Booking(slot_id=slot_id, student_id=student_id, topic=topic, status=status)
        session.add(booking)
        session.commit()
        return booking.id


@pytest.fixture
def schedule(db_engine):
    """Sara's four blocks, Omar's one block, and the bookings on them."""
    sara_id, sara_slots = add_block(
        db_engine, SARA, SARA_START, SLOT_COUNT, SARA_LOCATION
    )
    cancelled_id, cancelled_slots = add_block(
        db_engine, SARA, CANCELLED_START, TWO_SLOTS, CANCELLED_LOCATION, status="cancelled"
    )
    ended_id, ended_slots = add_block(db_engine, SARA, ENDED_START, TWO_SLOTS, ENDED_LOCATION)
    running_id, running_slots = add_block(
        db_engine, SARA, RUNNING_START, TWO_SLOTS, RUNNING_LOCATION
    )
    omar_id, omar_slots = add_block(db_engine, OMAR, OMAR_START, TWO_SLOTS, OMAR_LOCATION)

    add_booking(db_engine, STUDENT_A, sara_slots[0], "HW1")
    add_booking(db_engine, STUDENT_B, sara_slots[1], "T-gone", status="cancelled_by_student")
    add_booking(db_engine, STUDENT_B, omar_slots[0], "T-omar")

    return {
        "sara": sara_id,
        "sara_slots": sara_slots,
        "cancelled": cancelled_id,
        "cancelled_slots": cancelled_slots,
        "ended": ended_id,
        "ended_slots": ended_slots,
        "running": running_id,
        "running_slots": running_slots,
        "omar": omar_id,
        "omar_slots": omar_slots,
    }


def login(client, person):
    return client.post(
        "/login",
        data={"email": person["email"], "password": person["password"]},
        follow_redirects=False,
    )


def booking_snapshot(db_engine):
    """Every booking row's id, status and topic, in id order."""
    with Session(db_engine) as session:
        return sorted(session.execute(select(Booking.id, Booking.status, Booking.topic)).all())


def block_snapshot(db_engine):
    """Every block row's id and status, in id order."""
    with Session(db_engine) as session:
        return sorted(session.execute(select(Block.id, Block.status)).all())


# --- a/b/c. a booked slot shows its student and topic, an open slot shows neither ----


def test_a_a_booked_slot_shows_the_student_and_the_topic(client, schedule):
    login(client, SARA)

    page = client.get("/instructor")

    assert page.status_code == 200
    assert "10:00-10:15: booked" in page.text
    assert "Lina Hassan" in page.text
    assert "HW1" in page.text


def test_b_an_open_slot_shows_no_student(client, schedule):
    login(client, SARA)

    page = client.get("/instructor")

    assert "10:15-10:30: open" in page.text
    assert "T-gone" not in page.text
    assert "Yousef Ali" not in page.text


def test_c_every_slot_of_a_cancelled_block_reads_cancelled(client, schedule):
    login(client, SARA)

    page = client.get("/instructor")

    assert "10:00-10:15: cancelled" in page.text
    assert "10:15-10:30: cancelled" in page.text


# --- d. the boundary: a block is upcoming while its end is ahead of now --------------


def test_d_a_block_that_ended_exactly_now_is_hidden_and_a_running_one_is_shown(
    client, schedule
):
    login(client, SARA)

    page = client.get("/instructor")

    assert ENDED_LOCATION not in page.text
    assert RUNNING_LOCATION in page.text


# --- e. another instructor's blocks and bookings stay private ------------------------


def test_e_another_instructors_bookings_are_not_shown(client, schedule):
    login(client, SARA)

    page = client.get("/instructor")

    assert "T-omar" not in page.text
    assert OMAR_LOCATION not in page.text


# --- f. earliest block first --------------------------------------------------------


def test_f_blocks_are_listed_earliest_first(client, schedule):
    login(client, SARA)

    page = client.get("/instructor")

    running_at = page.text.index(RUNNING_LOCATION)
    sara_at = page.text.index(SARA_LOCATION)
    cancelled_at = page.text.index(CANCELLED_LOCATION)
    assert running_at < sara_at < cancelled_at


# --- g. the page is display only ----------------------------------------------------


def test_g_viewing_the_page_changes_nothing(client, db_engine, schedule):
    login(client, SARA)
    bookings_before = booking_snapshot(db_engine)
    blocks_before = block_snapshot(db_engine)

    client.get("/instructor")

    assert booking_snapshot(db_engine) == bookings_before
    assert block_snapshot(db_engine) == blocks_before


# --- h. a student never sees the instructor page ------------------------------------


def test_h_a_student_cannot_open_the_instructor_page(client, schedule):
    login(client, STUDENT_A)

    assert client.get("/instructor").status_code == 403
