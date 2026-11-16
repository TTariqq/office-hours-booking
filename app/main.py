import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from app.auth.deps import LoginRequired
from app.db import Base, engine as default_engine
from app.models import models  # importing the models registers their tables on Base
from app.routes import auth as auth_routes
from app.routes import pages as page_routes
from app.web.templating import WEB_DIR, templates

# Used only when OHB_SECRET_KEY is missing, so local development still runs.
DEV_SECRET_KEY = "dev-only-insecure-key-set-OHB_SECRET_KEY-in-production"


def create_app(db_engine=default_engine):
    """Build the app. Tests pass their own engine so they use a temporary database."""

    @asynccontextmanager
    async def lifespan(app):
        # Runs once, when the app starts: make sure the tables exist.
        Base.metadata.create_all(bind=db_engine)
        yield

    app = FastAPI(title="Office Hours Booking", lifespan=lifespan)

    secret_key = os.environ.get("OHB_SECRET_KEY")
    if not secret_key:
        logging.warning("OHB_SECRET_KEY is not set; using the insecure development key.")
        secret_key = DEV_SECRET_KEY

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret_key,
        same_site="lax",
        https_only=False,
    )

    # Dependencies and routes get their sessions from this app's own engine.
    app.state.session_factory = sessionmaker(bind=db_engine)

    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    @app.exception_handler(LoginRequired)
    def redirect_to_login(request, exc):
        return RedirectResponse("/login", status_code=303)

    @app.get("/")
    def home(request: Request):
        return templates.TemplateResponse(request, "home.html")

    app.include_router(auth_routes.router)
    app.include_router(page_routes.router)

    return app


app = create_app()
