from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import Base, engine as default_engine
from app.models import models  # importing the models registers their tables on Base

WEB_DIR = Path(__file__).resolve().parent / "web"
templates = Jinja2Templates(directory=WEB_DIR / "templates")


def create_app(db_engine=default_engine):
    """Build the app. Tests pass their own engine so they use a temporary database."""

    @asynccontextmanager
    async def lifespan(app):
        # Runs once, when the app starts: make sure the tables exist.
        Base.metadata.create_all(bind=db_engine)
        yield

    app = FastAPI(title="Office Hours Booking", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.get("/")
    def home(request: Request):
        return templates.TemplateResponse(request, "home.html")

    return app


app = create_app()
