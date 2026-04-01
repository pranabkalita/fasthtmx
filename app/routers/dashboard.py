from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from app.dependencies import get_current_user
from app.db.models import User
from app.templating import templates

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, current_user: User = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "title": "Dashboard",
            "user": current_user,
        },
    )
