from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import pytest

from app.db import Base, make_engine
from app.domain.bookings import BookingError, check_slot_bookable, clean_topic
from app.main import create_app
from app.models.models import Block, Booking, Slot, User
from scripts.seed import SEED_USERS, seed

INSTRUCTOR = next(person for person in SEED_USERS if person["role"] == "instructor")
STUDENTS = [person for person in SEED_USERS if person["role"] == "student"]
STUDENT_A, STUDENT_B = STUDENTS

FIXED_NOW = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

# The published block: 10:00-11:00 Riyadh, four 15 minute slots, in Room 2A.
BLOCK_START = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
BLOCK_END = datetime(2026, 11, 3, 8, 0, tzinfo=timezone.utc)
SLOT_LENGTH_MIN = 15
SLOT_COUNT = 4

SLOT_TAKEN = "This slot was just booked by someone else"
SLOT_GONE = "This slot is no longer available"
TOPIC_TOO_LONG = "topic must be at most 200 characters"


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
        test_client.app.state.clock = lambda: FIXED_NOW
        yield test_client


@pytest.fixture
def slot_ids(db_engine):
    """Insert the instructor's block and its four slots directly. Returns the slot ids."""
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
        return session.scalars(select(Slot.id).order_by(Slot.start_utc)).all()


def login(client, person):
    return client.post(
        "/login",
        data={"email": person["email"], "password": person["password"]},
        follow_redirects=False,
    )


def user_id(db_engine, person):
    with Session(db_engine) as session:
        return session.scalar(select(User.id).where(User.email == person["email"]))


def count_bookings(db_engine):
    with Session(db_engine) as session:
        return session.scalar(select(func.count()).select_from(Booking))


def all_bookings(db_engine):
    with Session(db_engine) as session:
        return session.scalars(select(Booking)).all()


def book(client, slot_id, topic=""):
    return client.post(
        "/student/bookings",
        data={"slot_id": str(slot_id), "topic": topic},
        follow_redirects=False,
    )


# --- domain rules -------------------------------------------------------------------


def test_clean_topic_strips_spaces():
    assert clean_topic("  HW1 question  ") == "HW1 question"


def test_clean_topic_allows_an_empty_topic():
    assert clean_topic("") == ""
    assert clean_topic("   ") == ""


def test_clean_topic_accepts_200_characters():
    assert len(clean_topic("x" * 200)) == 200


def test_clean_topic_rejects_201_characters():
    with pytest.raises(BookingError, match=TOPIC_TOO_LONG):
        clean_topic("x" * 201)


def test_clean_topic_measures_the_stripped_topic():
    assert len(clean_topic("  " + "x" * 200 + "  ")) == 200


def test_a_future_slot_in_an_open_block_is_bookable():
    check_slot_bookable(BLOCK_START, "open", FIXED_NOW)


def test_a_slot_that_starts_now_is_not_bookable():
    with pytest.raises(BookingError, match=SLOT_GONE):
        check_slot_bookable(BLOCK_START, "open", BLOCK_START)


def test_a_slot_in_a_cancelled_block_is_not_bookable():
    with pytest.raises(BookingError, match=SLOT_GONE):
        check_slot_bookable(BLOCK_START, "cancelled", FIXED_NOW)


@pytest.mark.parametrize(
    "slot_start,now",
    [
        (datetime(2026, 11, 3, 7), FIXED_NOW),
        (BLOCK_START, datetime(2026, 11, 1, 8)),
    ],
)
def test_naive_datetimes_are_rejected(slot_start, now):
    with pytest.raises(ValueError):
        check_slot_bookable(slot_start, "open", now)


# --- the student page ---------------------------------------------------------------


def test_student_sees_open_slots_and_books_one(client, db_engine, slot_ids):
    login(client, STUDENT_A)
    first_slot = slot_ids[0]

    page = client.get("/student")
    assert page.status_code == 200
    assert INSTRUCTOR["name"] in page.text
    assert "10:00" in page.text
    assert "Room 2A" in page.text
    assert f'name="slot_id" value="{first_slot}"' in page.text

    response = book(client, first_slot, "HW1 question")

    assert response.status_code == 303
    assert response.headers["location"] == "/student"

    bookings = all_bookings(db_engine)
    assert len(bookings) == 1
    assert bookings[0].slot_id == first_slot
    assert bookings[0].student_id == user_id(db_engine, STUDENT_A)
    assert bookings[0].topic == "HW1 question"
    assert bookings[0].status == "active"

    page = client.get("/student")
    assert "HW1 question" in page.text
    assert f'name="slot_id" value="{first_slot}"' not in page.text


def test_a_second_student_is_told_the_slot_was_just_taken(client, db_engine, slot_ids):
    first_slot = slot_ids[0]

    login(client, STUDENT_A)
    assert book(client, first_slot, "HW1 question").status_code == 303

    login(client, STUDENT_B)
    response = book(client, first_slot, "also mine")

    assert response.status_code == 409
    assert SLOT_TAKEN in response.text
    assert count_bookings(db_engine) == 1

    # The slot is gone from B's list of open slots too.
    assert f'name="slot_id" value="{first_slot}"' not in client.get("/student").text


def test_a_topic_of_200_characters_is_saved(client, db_engine, slot_ids):
    login(client, STUDENT_A)

    response = book(client, slot_ids[0], "x" * 200)

    assert response.status_code == 303
    bookings = all_bookings(db_engine)
    assert len(bookings) == 1
    assert len(bookings[0].topic) == 200


def test_a_topic_of_201_characters_is_rejected(client, db_engine, slot_ids):
    login(client, STUDENT_A)

    response = book(client, slot_ids[0], "x" * 201)

    assert response.status_code == 400
    assert TOPIC_TOO_LONG in response.text
    assert count_bookings(db_engine) == 0


def test_a_slot_that_already_started_is_gone(client, db_engine, slot_ids):
    # The clock now stands exactly on the first slot's start.
    client.app.state.clock = lambda: BLOCK_START
    login(client, STUDENT_A)

    page = client.get("/student")
    assert "10:00" not in page.text

    response = book(client, slot_ids[0], "too late")

    assert response.status_code == 400
    assert SLOT_GONE in response.text
    assert count_bookings(db_engine) == 0


def test_an_instructor_cannot_book_a_slot(client, db_engine, slot_ids):
    login(client, INSTRUCTOR)

    response = book(client, slot_ids[0], "not for me")

    assert response.status_code == 403
    assert count_bookings(db_engine) == 0


def test_an_unknown_slot_id_is_a_404(client, db_engine, slot_ids):
    login(client, STUDENT_A)

    response = book(client, 99999, "nowhere")

    assert response.status_code == 404
    assert count_bookings(db_engine) == 0


# --- the database guard -------------------------------------------------------------


def test_two_active_bookings_for_one_slot_are_rejected(db_engine, slot_ids):
    with Session(db_engine) as session:
        session.add(
            Booking(
                slot_id=slot_ids[0],
                student_id=user_id(db_engine, STUDENT_A),
                topic="first",
                status="active",
            )
        )
        session.add(
            Booking(
                slot_id=slot_ids[0],
                student_id=user_id(db_engine, STUDENT_B),
                topic="second",
                status="active",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    assert count_bookings(db_engine) == 0


def test_a_cancelled_booking_may_share_a_slot_with_an_active_one(db_engine, slot_ids):
    with Session(db_engine) as session:
        session.add(
            Booking(
                slot_id=slot_ids[0],
                student_id=user_id(db_engine, STUDENT_A),
                topic="dropped",
                status="cancelled",
            )
        )
        session.commit()

    with Session(db_engine) as session:
        session.add(
            Booking(
                slot_id=slot_ids[0],
                student_id=user_id(db_engine, STUDENT_B),
                topic="mine now",
                status="active",
            )
        )
        session.commit()

    assert count_bookings(db_engine) == 2
