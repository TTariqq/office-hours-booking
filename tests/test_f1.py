from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import pytest

from app.db import Base, make_engine
from app.main import create_app
from app.models.models import User
from scripts.seed import SEED_USERS, seed


@pytest.fixture
def db_engine(tmp_path):
    """A throwaway SQLite database for each test."""
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = make_engine(url)
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(db_engine):
    """A test client talking to an app that uses the throwaway database."""
    with TestClient(create_app(db_engine)) as test_client:
        yield test_client


def test_home_page_returns_the_heading(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Office Hours Booking" in response.text


def test_unknown_path_returns_404(client):
    response = client.get("/nothing-here")
    assert response.status_code == 404


def test_seed_twice_leaves_four_users(db_engine):
    with Session(db_engine) as session:
        seed(session)
        seed(session)

    with Session(db_engine) as session:
        user_count = session.scalar(select(func.count()).select_from(User))
    assert user_count == 4


def test_password_hash_is_never_the_plain_password(db_engine):
    with Session(db_engine) as session:
        seed(session)

    with Session(db_engine) as session:
        users = session.scalars(select(User)).all()

    plain_passwords = {person["password"] for person in SEED_USERS}
    assert len(users) == 4
    for user in users:
        assert user.password_hash not in plain_passwords
        assert user.password_hash.startswith("pbkdf2_sha256$")
