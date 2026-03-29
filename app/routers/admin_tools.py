from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db_session
from app.db.models import User
from app.dependencies import get_admin_user
from app.services.deferred_email_service import (
    get_deferred_email_overview,
    get_recent_deferred_email_jobs,
    requeue_failed_deferred_email_jobs,
)
from app.services.email_service import render_email_bodies
from app.services.flash_service import add_toast
from app.services.job_queue import get_recent_email_job_results, is_job_queue_healthy
from app.templating import templates

router = APIRouter(tags=["admin-tools"])
settings = get_settings()

EMAIL_PREVIEW_TEMPLATES = (
    "verify_account",
    "verify_account_resend",
    "reset_password",
    "verify_new_email",
)


def _preview_context(template_name: str, request: Request) -> dict[str, str | int]:
    return {
        "subject": f"Preview: {template_name.replace('_', ' ').title()}",
        "preheader": "Development preview for FastAuth email templates.",
        "action_url": f"{settings.app_url}/preview/{template_name}?token=sample-token",
        "expires_hours": 24,
        "user_name": "Pranab",
        "website_url": settings.app_url,
        "support_email": settings.mail_from,
        "product_name": settings.app_name,
        "year": datetime.now(UTC).year,
    }


@router.get("/dev/email-previews", response_class=HTMLResponse)
async def email_previews_index(
    request: Request,
    current_user: User = Depends(get_admin_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dev/email_previews_index.html",
        {
            "title": "Email Previews",
            "user": current_user,
            "template_names": EMAIL_PREVIEW_TEMPLATES,
        },
    )


@router.get("/dev/email-previews/{template_name}", response_class=HTMLResponse)
async def email_preview_html(
    template_name: str,
    request: Request,
    current_user: User = Depends(get_admin_user),
) -> HTMLResponse:
    if template_name not in EMAIL_PREVIEW_TEMPLATES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown template")
    html_body, _ = render_email_bodies(template_name=template_name, context=_preview_context(template_name, request))
    return templates.TemplateResponse(
        request,
        "dev/email_preview_html.html",
        {
            "title": f"Email Preview: {template_name}",
            "user": current_user,
            "template_name": template_name,
            "html_body": html_body,
        },
    )


@router.get("/dev/email-previews/{template_name}/text", response_class=HTMLResponse)
async def email_preview_text(
    template_name: str,
    request: Request,
    current_user: User = Depends(get_admin_user),
) -> HTMLResponse:
    if template_name not in EMAIL_PREVIEW_TEMPLATES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown template")
    _, text_body = render_email_bodies(template_name=template_name, context=_preview_context(template_name, request))
    return templates.TemplateResponse(
        request,
        "dev/email_preview_text.html",
        {
            "title": f"Email Preview Text: {template_name}",
            "user": current_user,
            "template_name": template_name,
            "text_body": text_body,
        },
    )


@router.get("/admin/queue-status", response_class=HTMLResponse)
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
        "dev/queue_status.html",
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


@router.post("/admin/queue-status/deferred/requeue-failed")
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
