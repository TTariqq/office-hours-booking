from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from app.auth.deps import require_role
from app.models.models import User
from app.web.templating import templates

router = APIRouter()


@router.get("/student")
def student_home(request: Request, user: User = Depends(require_role("student"))):
    return templates.TemplateResponse(request, "student_home.html", {"user": user})


@router.get("/instructor")
def instructor_home(request: Request, user: User = Depends(require_role("instructor"))):
    return templates.TemplateResponse(request, "instructor_home.html", {"user": user})


@router.post("/instructor/blocks")
def create_block(user: User = Depends(require_role("instructor"))):
    # Placeholder: the real block creation arrives in a later feature.
    return PlainTextResponse("not built yet", status_code=501)
