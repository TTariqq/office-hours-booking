from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# The database file lives next to the project root.
DATABASE_URL = "sqlite:///ohb.db"


class Base(DeclarativeBase):
    """Parent class for every ORM model."""


def make_engine(database_url):
    """Create an engine for the given URL; tests pass their own temporary file."""
    connect_args = {}
    if database_url.startswith("sqlite"):
        # FastAPI serves requests from several threads, so SQLite must allow that.
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args)


engine = make_engine(DATABASE_URL)

# A sessionmaker is a factory: SessionLocal() gives one database session.
SessionLocal = sessionmaker(bind=engine)
