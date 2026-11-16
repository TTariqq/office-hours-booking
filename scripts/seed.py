"""Add demo users to the database. Running it twice changes nothing."""

import hashlib
import os
import sys
from pathlib import Path

# Running "python scripts/seed.py" puts scripts/ on the import path, so add the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Base, SessionLocal, engine
from app.models.models import User

# How many times the password hash function runs. Higher is slower and safer.
PBKDF2_ROUNDS = 100_000

SEED_USERS = [
    {
        "name": "Dr. Sara Ahmed",
        "email": "sara.ahmed@example.edu",
        "role": "instructor",
        "password": "instructor-sara-pass",
    },
    {
        "name": "Dr. Omar Khalid",
        "email": "omar.khalid@example.edu",
        "role": "instructor",
        "password": "instructor-omar-pass",
    },
    {
        "name": "Lina Hassan",
        "email": "lina.hassan@example.edu",
        "role": "student",
        "password": "student-lina-pass",
    },
    {
        "name": "Yousef Ali",
        "email": "yousef.ali@example.edu",
        "role": "student",
        "password": "student-yousef-pass",
    },
]


def hash_password(password):
    """Hash a password with a fresh random salt. The salt is stored with the hash."""
    salt = os.urandom(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt.hex()}${key.hex()}"


def seed(session):
    """Insert the demo users that are missing yet. Returns how many were added."""
    added = 0
    for person in SEED_USERS:
        already_there = session.scalar(select(User).where(User.email == person["email"]))
        if already_there is not None:
            continue
        session.add(
            User(
                email=person["email"],
                password_hash=hash_password(person["password"]),
                role=person["role"],
                name=person["name"],
            )
        )
        added += 1
    session.commit()
    return added


def main():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as session:
        added = seed(session)
    print(f"Seed finished: {added} new users.")


if __name__ == "__main__":
    main()
