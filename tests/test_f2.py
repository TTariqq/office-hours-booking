from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import pytest

from app.db import Base, make_engine
from app.main import create_app
from app.models.models import Block
from scripts.seed import SEED_USERS, seed

INSTRUCTOR = next(person for person in SEED_USERS if person["role"] == "instructor")
STUDENT = next(person for person in SEED_USERS if person["role"] == "student")


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
    """A test client talking to an app that uses the throwaway database."""
    with TestClient(create_app(db_engine)) as test_client:
        yield test_client


def login(client, person, password=None):
    """POST the login form and return the response without following the redirect."""
    return client.post(
        "/login",
        data={"email": person["email"], "password": password or person["password"]},
        follow_redirects=False,
    )


def count_blocks(db_engine):
    with Session(db_engine) as session:
        return session.scalar(select(func.count()).select_from(Block))


def test_instructor_login_redirects_to_instructor_home(client):
    response = login(client, INSTRUCTOR)

    assert response.status_code == 303
    assert response.headers["location"] == "/instructor"
    assert "session" in response.cookies

    home = client.get("/instructor")
    assert home.status_code == 200
    assert INSTRUCTOR["name"] in home.text


def test_student_login_redirects_to_student_home(client):
    response = login(client, STUDENT)

    assert response.status_code == 303
    assert response.headers["location"] == "/student"
    assert "session" in response.cookies


def test_wrong_password_is_rejected_without_a_session(client):
    response = login(client, STUDENT, password="not-the-password")

    assert response.status_code == 200
    assert "Invalid email or password" in response.text
    assert "session" not in response.cookies


def test_unknown_email_is_rejected_without_a_session(client):
    response = client.post(
        "/login",
        data={"email": "nobody@example.edu", "password": "whatever"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Invalid email or password" in response.text
    assert "session" not in response.cookies


def test_student_cannot_create_blocks(client, db_engine):
    login(client, STUDENT)
    blocks_before = count_blocks(db_engine)

    response = client.post("/instructor/blocks", follow_redirects=False)

    assert response.status_code == 403
    assert count_blocks(db_engine) == blocks_before


def test_logout_ends_the_session(client):
    login(client, INSTRUCTOR)
    assert client.get("/instructor").status_code == 200

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 303
    assert logout_response.headers["location"] == "/login"

    response = client.get("/instructor", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_instructor_page_requires_login(client):
    response = client.get("/instructor", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"
