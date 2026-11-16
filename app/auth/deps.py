from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.models.models import User


class LoginRequired(Exception):
    """Raised when a page needs a logged-in user but the session has none.

    app/main.py turns this exception into a 303 redirect to /login.
    """


def get_db(request: Request):
    """One database session per request, built from this app's own engine."""
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_current_user(request: Request, db: Session = Depends(get_db)):
    """The logged-in User, or None when the session has no user id."""
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    return db.get(User, user_id)


def require_role(role):
    """Dependency factory: allow only one role, redirect guests to /login."""

    def check_role(user: User = Depends(get_current_user)):
        if user is None:
            raise LoginRequired()
        if user.role != role:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return check_role
