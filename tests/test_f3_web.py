from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import pytest

from app.db import Base, make_engine
from app.main import create_app
from app.models.models import Block, Slot
from app.timeutil import as_utc, to_riyadh
from scripts.seed import SEED_USERS, seed

INSTRUCTOR = next(person for person in SEED_USERS if person["role"] == "instructor")
STUDENT = next(person for person in SEED_USERS if person["role"] == "student")

FIXED_NOW = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

VALID_FORM = {
    "date": "2026-11-03",
    "start_time": "10:00",
    "end_time": "11:00",
    "slot_length": "15",
    "location": "Room 2A",
}


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


def login(client, person):
    return client.post(
        "/login",
        data={"email": person["email"], "password": person["password"]},
        follow_redirects=False,
    )


def count_rows(db_engine, model):
    with Session(db_engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_instructor_publishes_a_block_with_four_slots(client, db_engine):
    login(client, INSTRUCTOR)

    response = client.post("/instructor/blocks", data=VALID_FORM, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/instructor"
    assert count_rows(db_engine, Block) == 1
    assert count_rows(db_engine, Slot) == 4

    with Session(db_engine) as session:
        slot_starts = session.scalars(select(Slot.start_utc).order_by(Slot.start_utc)).all()

    assert [as_utc(start) for start in slot_starts] == [
        datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 3, 7, 15, tzinfo=timezone.utc),
        datetime(2026, 11, 3, 7, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 3, 7, 45, tzinfo=timezone.utc),
    ]

    page = client.get("/instructor")
    assert page.status_code == 200
    assert "10:00" in page.text
    assert "10:45" in page.text
    assert "Room 2A" in page.text


def test_a_bad_slot_length_saves_nothing(client, db_engine):
    login(client, INSTRUCTOR)

    response = client.post(
        "/instructor/blocks",
        data={**VALID_FORM, "slot_length": "25"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "slot length must be 10, 15, 20 or 30" in response.text
    assert count_rows(db_engine, Block) == 0
    assert count_rows(db_engine, Slot) == 0


def test_sqlite_values_are_read_back_as_aware_utc(client, db_engine):
    login(client, INSTRUCTOR)
    client.post("/instructor/blocks", data=VALID_FORM, follow_redirects=False)

    with Session(db_engine) as session:
        stored = session.scalars(select(Slot.start_utc).order_by(Slot.start_utc)).first()

    assert stored.tzinfo is None  # SQLite does not keep the time zone
    assert as_utc(stored) == datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
    assert to_riyadh(stored).strftime("%H:%M") == "10:00"


def test_a_student_cannot_publish_a_block(client, db_engine):
    login(client, STUDENT)

    response = client.post("/instructor/blocks", data=VALID_FORM, follow_redirects=False)

    assert response.status_code == 403
    assert count_rows(db_engine, Block) == 0
    assert count_rows(db_engine, Slot) == 0
