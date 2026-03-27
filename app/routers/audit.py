from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.db.database import get_db_session
from app.db.models import AuditLog, User
from app.dependencies import get_admin_user

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")


@router.get("/audit-logs", response_class=HTMLResponse)
async def audit_logs(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    rows = (
        await db.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(100))
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "dashboard/audit_logs.html",
        {"title": "Audit Logs", "logs": rows, "user": current_user},
    )
