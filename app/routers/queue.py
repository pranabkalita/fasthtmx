from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db_session
from app.db.models import User
from app.dependencies import get_admin_user
from app.services.deferred_email_service import (
    get_deferred_email_overview,
    get_recent_deferred_email_jobs,
    requeue_failed_deferred_email_jobs,
)
from app.services.flash_service import add_toast
from app.services.job_queue import get_recent_email_job_results, is_job_queue_healthy
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["queue"])


@router.get("/queue-status", response_class=HTMLResponse)
async def queue_status_page(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    healthy = await is_job_queue_healthy()
    recent_email_jobs = await get_recent_email_job_results(limit=12)
    deferred_overview = await get_deferred_email_overview(db)
    recent_deferred_jobs = await get_recent_deferred_email_jobs(db, limit=12)
    return templates.TemplateResponse(
        request,
        "admin/queue/status.html",
        {
            "title": "Queue Status",
            "user": current_user,
            "active_page": "queue_status",
            "queue_healthy": healthy,
            "recent_email_jobs": recent_email_jobs,
            "deferred_overview": deferred_overview,
            "recent_deferred_jobs": recent_deferred_jobs,
        },
    )


@router.post("/queue-status/deferred/requeue-failed")
async def requeue_failed_deferred_jobs(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    _ = current_user
    count = await requeue_failed_deferred_email_jobs(db, limit=200)
    if count > 0:
        add_toast(request, type="success", message=f"Requeued {count} failed deferred email jobs.")
    else:
        add_toast(request, type="success", message="No failed deferred email jobs to requeue.")
    return RedirectResponse(url="/admin/queue-status", status_code=status.HTTP_303_SEE_OTHER)
