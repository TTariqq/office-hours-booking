from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.domain.blocks import BlockError, check_block_cancellable
from app.main import create_app
from app.models.models import Block, Booking, Slot, User
from scripts.seed import SEED_USERS, seed

SARA, OMAR = [person for person in SEED_USERS if person["role"] == "instructor"]
STUDENT_A, STUDENT_B = [person for person in SEED_USERS if person["role"] == "student"]

# The clock every test runs on.
NOW = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

SLOT_LENGTH_MIN = 15

# Dr. Sara: four slots on 2026-11-03, 07:00-08:00 UTC. 10:00-10:45 in Riyadh.
SARA_START = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
SARA_SLOT_COUNT = 4
LOCATION = "Room 2A"

# Dr. Sara: two slots on 2026-11-01, the block starting exactly at the frozen now.
NOW_START = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)
NOW_SLOT_COUNT = 2

ALREADY_CANCELLED = "This block is already cancelled"
ONLY_FUTURE = "Only future blocks can be cancelled"
NOT_AVAILABLE = "This slot is no longer available"
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
def sara_block(db_engine):
    """Dr. Sara's future block and her block that starts exactly now."""
    sara_id, sara_slots = add_block(db_engine, SARA, SARA_START, SARA_SLOT_COUNT, LOCATION)
    now_id, now_slots = add_block(db_engine, SARA, NOW_START, NOW_SLOT_COUNT, "Room 3B")
    return {"id": sara_id, "slots": sara_slots, "now_id": now_id, "now_slots": now_slots}


@pytest.fixture
def booked(db_engine, sara_block):
    """Two active bookings on Sara's block, plus one the student already cancelled."""
    return {
        "a_active": add_booking(db_engine, STUDENT_A, sara_block["slots"][0], "T-active-a"),
        "b_active": add_booking(db_engine, STUDENT_B, sara_block["slots"][1], "T-active-b"),
        "a_cancelled": add_booking(
            db_engine,
            STUDENT_A,
            sara_block["slots"][2],
            "T-cancelled-a",
            status="cancelled_by_student",
        ),
    }


def login(client, person):
    return client.post(
        "/login",
        data={"email": person["email"], "password": person["password"]},
        follow_redirects=False,
    )


def cancel_url(block_id):
    return f"/instructor/blocks/{block_id}/cancel"


def block_status(db_engine, block_id):
    with Session(db_engine) as session:
        return session.scalar(select(Block.status).where(Block.id == block_id))


def booking_snapshot(db_engine):
    """Every booking row's id and status, in id order."""
    with Session(db_engine) as session:
        return sorted(session.execute(select(Booking.id, Booking.status)).all())


def booking_status(db_engine, booking_id):
    with Session(db_engine) as session:
        return session.scalar(select(Booking.status).where(Booking.id == booking_id))


# --- a. the instructor cancels a future block (SC-14) -------------------------------


def test_a_cancelling_a_future_block_cancels_its_active_bookings(
    client, db_engine, sara_block, booked
):
    login(client, SARA)
    before = booking_snapshot(db_engine)

    response = client.post(cancel_url(sara_block["id"]), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/instructor"
    assert block_status(db_engine, sara_block["id"]) == "cancelled"
    assert booking_status(db_engine, booked["a_active"]) == "cancelled_by_instructor"
    assert booking_status(db_engine, booked["b_active"]) == "cancelled_by_instructor"
    assert booking_status(db_engine, booked["a_cancelled"]) == "cancelled_by_student"

    after = booking_snapshot(db_engine)
    assert len(after) == len(before) == 3
    assert [row[0] for row in after] == [row[0] for row in before]


def test_a_the_student_sees_the_cancellation_and_cannot_book_the_freed_slot(
    client, db_engine, sara_block, booked
):
    login(client, SARA)
    client.post(cancel_url(sara_block["id"]), follow_redirects=False)

    login(client, STUDENT_A)
    page = client.get("/student")

    assert CANCELLED_BY_INSTRUCTOR in page.text
    assert f'name="slot_id" value="{sara_block["slots"][3]}"' not in page.text

    response = client.post(
        "/student/bookings",
        data={"slot_id": str(sara_block["slots"][3]), "topic": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert NOT_AVAILABLE in response.text


# --- b/c. only the owner may cancel, and unknown ids are the same answer -------------


def test_b_another_instructor_cannot_cancel_the_block(client, db_engine, sara_block, booked):
    login(client, OMAR)
    before = booking_snapshot(db_engine)

    response = client.post(cancel_url(sara_block["id"]), follow_redirects=False)

    assert response.status_code == 404
    assert block_status(db_engine, sara_block["id"]) == "open"
    assert booking_snapshot(db_engine) == before


def test_c_an_unknown_block_id_is_not_found(client, db_engine):
    login(client, SARA)

    assert client.post(cancel_url(99999), follow_redirects=False).status_code == 404


# --- d. a block starting exactly now is not future ----------------------------------


def test_d_a_block_starting_exactly_now_cannot_be_cancelled(client, db_engine, sara_block):
    login(client, SARA)

    response = client.post(cancel_url(sara_block["now_id"]), follow_redirects=False)

    assert response.status_code == 400
    assert ONLY_FUTURE in response.text
    assert block_status(db_engine, sara_block["now_id"]) == "open"


# --- e. cancelling twice -----------------------------------------------------------


def test_e_cancelling_the_same_block_twice_is_refused(client, db_engine, sara_block):
    login(client, SARA)

    first = client.post(cancel_url(sara_block["id"]), follow_redirects=False)
    second = client.post(cancel_url(sara_block["id"]), follow_redirects=False)

    assert first.status_code == 303
    assert second.status_code == 400
    assert ALREADY_CANCELLED in second.text
    assert block_status(db_engine, sara_block["id"]) == "cancelled"


# --- f. a student may not cancel ---------------------------------------------------


def test_f_a_student_cannot_cancel_a_block(client, db_engine, sara_block, booked):
    login(client, STUDENT_A)
    before = booking_snapshot(db_engine)

    response = client.post(cancel_url(sara_block["id"]), follow_redirects=False)

    assert response.status_code == 403
    assert block_status(db_engine, sara_block["id"]) == "open"
    assert booking_snapshot(db_engine) == before


# --- g. the page offers the form only while the block is cancellable ----------------


def test_g_the_cancel_form_disappears_after_cancelling(client, db_engine, sara_block):
    login(client, SARA)
    form = f'action="{cancel_url(sara_block["id"])}"'

    before = client.get("/instructor")
    assert form in before.text
    assert "cancelled" not in before.text

    client.post(cancel_url(sara_block["id"]), follow_redirects=False)

    after = client.get("/instructor")
    assert form not in after.text
    assert "cancelled" in after.text


# --- h. a cancelled block does not block a new one at the same time -----------------


def test_h_the_same_hours_can_be_published_again_after_cancelling(client, db_engine, sara_block):
    login(client, SARA)
    client.post(cancel_url(sara_block["id"]), follow_redirects=False)

    response = client.post(
        "/instructor/blocks",
        data={
            "date": "2026-11-03",
            "start_time": "10:00",
            "end_time": "11:00",
            "slot_length": "15",
            "location": LOCATION,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/instructor"

    with Session(db_engine) as session:
        open_blocks = session.scalars(
            select(Block.status).where(Block.instructor_id == user_id(db_engine, SARA))
        ).all()
    assert sorted(open_blocks) == ["cancelled", "open", "open"]


# --- domain rule -------------------------------------------------------------------


def test_a_future_open_block_can_be_cancelled():
    check_block_cancellable(
        datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc), "open", NOW
    )


def test_a_block_starting_exactly_now_cannot_be_cancelled():
    with pytest.raises(BlockError, match="Only future blocks can be cancelled"):
        check_block_cancellable(NOW, "open", NOW)


def test_a_past_block_cannot_be_cancelled():
    with pytest.raises(BlockError, match="Only future blocks can be cancelled"):
        check_block_cancellable(
            datetime(2026, 10, 30, 7, 0, tzinfo=timezone.utc), "open", NOW
        )


def test_an_already_cancelled_block_cannot_be_cancelled():
    with pytest.raises(BlockError, match="This block is already cancelled"):
        check_block_cancellable(
            datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc), "cancelled", NOW
        )


def test_naive_datetimes_are_rejected():
    naive = datetime(2026, 11, 3, 7, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        check_block_cancellable(naive, "open", NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        check_block_cancellable(datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc), "open", naive)
