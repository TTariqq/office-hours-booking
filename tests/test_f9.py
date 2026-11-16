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

# Dr. Sara: four slots on 2026-11-03, 07:00-08:00 UTC. 10:00-10:45 in Riyadh.
FUTURE_START = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
FUTURE_SLOT_COUNT = 4
FUTURE_LOCATION = "Room 2A"

# Dr. Sara: two slots already in the past on 2026-10-30.
PAST_START = datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc)
PAST_SLOT_COUNT = 2
PAST_LOCATION = "Room 2A"

# Dr. Omar: two slots on 2026-11-01, the first one starting exactly at the frozen now.
NOW_START = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
NOW_SLOT_COUNT = 2
NOW_LOCATION = "Room 3B"

BOOKED = "Booked"
CANCELLED_BY_STUDENT = "Cancelled by you"
CANCELLED_BY_INSTRUCTOR = "Cancelled by instructor"


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
        "future": add_block(db_engine, SARA, FUTURE_START, FUTURE_SLOT_COUNT, FUTURE_LOCATION),
        "past": add_block(db_engine, SARA, PAST_START, PAST_SLOT_COUNT, PAST_LOCATION),
        "now": add_block(db_engine, OMAR, NOW_START, NOW_SLOT_COUNT, NOW_LOCATION),
    }


def add_booking(db_engine, student, slot_id, topic, status="active"):
    """Insert a booking directly, bypassing the routes. Returns its id."""
    with Session(db_engine) as session:
        student_id = session.scalar(select(User.id).where(User.email == student["email"]))
        booking = Booking(slot_id=slot_id, student_id=student_id, topic=topic, status=status)
        session.add(booking)
        session.commit()
        return booking.id


def login(client, person):
    return client.post(
        "/login",
        data={"email": person["email"], "password": person["password"]},
        follow_redirects=False,
    )


def upcoming_and_past(page_text):
    """The text before 'id="past"' is the Upcoming list, the text after it is Past."""
    upcoming, past = page_text.split('id="past"')
    return upcoming, past


def row_text(section, topic):
    """The stretch of a list around this topic, wide enough to hold the whole row."""
    at = section.index(topic)
    return section[max(0, at - 300) : at + 300]


def cancel_action(booking_id):
    return f'action="/student/bookings/{booking_id}/cancel"'


def booking_snapshot(db_engine):
    """Every booking row's id and status, in id order."""
    with Session(db_engine) as session:
        return sorted(session.execute(select(Booking.id, Booking.status)).all())


# --- upcoming bookings --------------------------------------------------------------


def test_a_an_active_upcoming_booking_is_booked_and_can_be_cancelled(
    client, db_engine, slots
):
    login(client, STUDENT_A)
    booking_id = add_booking(db_engine, STUDENT_A, slots["future"][0], "T-active")

    page = client.get("/student")
    upcoming, past = upcoming_and_past(page.text)

    assert "T-active" in upcoming
    assert "T-active" not in past
    row = row_text(upcoming, "T-active")
    assert BOOKED in row
    assert "10:00" in row
    assert cancel_action(booking_id) in page.text


def test_b_a_student_cancellation_stays_upcoming_without_a_cancel_form(
    client, db_engine, slots
):
    login(client, STUDENT_A)
    booking_id = add_booking(
        db_engine, STUDENT_A, slots["future"][1], "T-cancelled", status="cancelled_by_student"
    )

    page = client.get("/student")
    upcoming, past = upcoming_and_past(page.text)

    assert "T-cancelled" in upcoming
    assert "T-cancelled" not in past
    assert CANCELLED_BY_STUDENT in row_text(upcoming, "T-cancelled")
    assert cancel_action(booking_id) not in page.text


def test_c_an_instructor_cancellation_is_labelled_and_has_no_cancel_form(
    client, db_engine, slots
):
    login(client, STUDENT_A)
    booking_id = add_booking(
        db_engine,
        STUDENT_A,
        slots["future"][2],
        "T-instructor",
        status="cancelled_by_instructor",
    )

    page = client.get("/student")
    upcoming, past = upcoming_and_past(page.text)

    assert "T-instructor" in upcoming
    assert "T-instructor" not in past
    assert CANCELLED_BY_INSTRUCTOR in row_text(upcoming, "T-instructor")
    assert cancel_action(booking_id) not in page.text


# --- past bookings ------------------------------------------------------------------


def test_d_an_active_booking_that_already_started_is_past_and_has_no_cancel_form(
    client, db_engine, slots
):
    login(client, STUDENT_A)
    booking_id = add_booking(db_engine, STUDENT_A, slots["past"][0], "T-past")

    page = client.get("/student")
    upcoming, past = upcoming_and_past(page.text)

    assert "T-past" in past
    assert "T-past" not in upcoming
    assert cancel_action(booking_id) not in page.text


def test_e_a_booking_starting_exactly_now_counts_as_past(client, db_engine, slots):
    login(client, STUDENT_A)
    booking_id = add_booking(db_engine, STUDENT_A, slots["now"][0], "T-now")

    page = client.get("/student")
    upcoming, past = upcoming_and_past(page.text)

    assert "T-now" in past
    assert "T-now" not in upcoming
    assert BOOKED in row_text(past, "T-now")
    assert cancel_action(booking_id) not in page.text


# --- one student sees only their own rows -------------------------------------------


def test_f_another_students_booking_is_not_shown(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_B, slots["future"][3], "T-other-student")

    page = client.get("/student")

    assert "T-other-student" not in page.text


# --- ordering -----------------------------------------------------------------------


def test_g_past_bookings_read_newest_first(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["past"][0], "T-past-early")
    add_booking(db_engine, STUDENT_A, slots["past"][1], "T-past-late")

    page = client.get("/student")
    upcoming, past = upcoming_and_past(page.text)

    assert "T-past-late" in past
    assert "T-past-early" in past
    # The later slot is 10:15 Riyadh, the earlier one 10:00, so the later row comes first.
    assert past.index("T-past-late") < past.index("T-past-early")
    assert "10:15" in row_text(past, "T-past-late")
    assert "10:00" in row_text(past, "T-past-early")


# --- the page only reads ------------------------------------------------------------


def test_h_viewing_the_page_changes_no_booking(client, db_engine, slots):
    login(client, STUDENT_A)
    add_booking(db_engine, STUDENT_A, slots["future"][0], "T-active")
    add_booking(
        db_engine, STUDENT_A, slots["future"][1], "T-cancelled", status="cancelled_by_student"
    )
    add_booking(db_engine, STUDENT_A, slots["past"][0], "T-past")
    before = booking_snapshot(db_engine)

    assert client.get("/student").status_code == 200

    assert booking_snapshot(db_engine) == before
    assert len(before) == 3


def test_i_an_instructor_cannot_open_the_student_page(client):
    login(client, SARA)

    assert client.get("/student").status_code == 403
