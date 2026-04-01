from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.db.models import User
from app.dependencies import get_admin_user
from app.services.email_service import render_email_bodies
from app.templating import templates

router = APIRouter(prefix="/dev", tags=["email"])
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


@router.get("/email-previews", response_class=HTMLResponse)
async def email_previews_index(
    request: Request,
    current_user: User = Depends(get_admin_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dev/email/index.html",
        {
            "title": "Email Previews",
            "user": current_user,
            "template_names": EMAIL_PREVIEW_TEMPLATES,
        },
    )


@router.get("/email-previews/{template_name}", response_class=HTMLResponse)
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
        "dev/email/preview_html.html",
        {
            "title": f"Email Preview: {template_name}",
            "user": current_user,
            "template_name": template_name,
            "html_body": html_body,
        },
    )


@router.get("/email-previews/{template_name}/text", response_class=HTMLResponse)
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
        "dev/email/preview_text.html",
        {
            "title": f"Email Preview Text: {template_name}",
            "user": current_user,
            "template_name": template_name,
            "text_body": text_body,
        },
    )
