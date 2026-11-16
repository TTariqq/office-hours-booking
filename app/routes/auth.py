from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_db
from app.auth.security import verify_password
from app.models.models import User
from app.web.templating import templates

router = APIRouter()

INVALID_LOGIN = "Invalid email or password"


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == email))

    # Same answer for an unknown email and a wrong password, so nobody can probe emails.
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": INVALID_LOGIN},
            status_code=200,
        )

    request.session.clear()
    request.session["user_id"] = user.id
    request.session["role"] = user.role

    if user.role == "instructor":
        return RedirectResponse("/instructor", status_code=303)
    return RedirectResponse("/student", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
