from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import quote_plus

from app.config import get_settings
from app.db.database import get_db_session
from app.dependencies import get_current_user
from app.db.models import User
from app.services.auth_service import create_email_verification_token
from app.services.audit_service import write_audit_log
from app.services.email_service import send_templated_email
from app.services.flash_service import add_toast
from app.templating import templates

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
async def dashboard(request: Request, current_user: User = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard/index.html",
        {
            "title": "Dashboard",
            "user": current_user,
        },
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, current_user: User = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard/profile.html",
        {
            "title": "Profile",
            "user": current_user,
        },
    )


@router.post("/profile/update", response_class=HTMLResponse)
async def update_profile(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    clean_name = full_name.strip()
    clean_email = email.strip().lower()

    if len(clean_name) > 120:
        return templates.TemplateResponse(
            request,
            "dashboard/profile.html",
            {
                "title": "Profile",
                "user": current_user,
                "error": "Name must be 120 characters or fewer.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        normalized_email = validate_email(clean_email, check_deliverability=False).normalized
    except EmailNotValidError:
        return templates.TemplateResponse(
            request,
            "dashboard/profile.html",
            {
                "title": "Profile",
                "user": current_user,
                "error": "Please enter a valid email address.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if normalized_email != current_user.email:
        existing = (
            await db.execute(
                select(User).where(User.email == normalized_email, User.id != current_user.id)
            )
        ).scalar_one_or_none()
        if existing:
            return templates.TemplateResponse(
                request,
                "dashboard/profile.html",
                {
                    "title": "Profile",
                    "user": current_user,
                    "error": "That email is already used by another account.",
                },
                status_code=status.HTTP_409_CONFLICT,
            )

    has_email_change = normalized_email != current_user.email
    current_user.full_name = clean_name
    current_user.email = normalized_email
    success_message = "Profile updated successfully."

    if has_email_change:
        current_user.is_verified = False

    await db.commit()
    await db.refresh(current_user)

    if has_email_change:
        signed_token, _ = await create_email_verification_token(db=db, user_id=current_user.id)
        verify_link = f"{settings.app_url}/verify-email?token={quote_plus(signed_token)}"
        await send_templated_email(
            subject="Verify your new email address",
            recipients=[current_user.email],
            template_name="verify_new_email",
            context={
                "subject": "Verify your new email address",
                "preheader": "Confirm your new email to keep account access.",
                "action_url": verify_link,
                "expires_hours": 24,
            },
        )
        success_message = "Profile updated. Verify your new email before next login."

    await write_audit_log(
        db,
        action="PROFILE_UPDATED_EMAIL_PENDING_VERIFY" if has_email_change else "PROFILE_UPDATED",
        target="user",
        user_id=current_user.id,
        request=request,
        details=f"email_changed={has_email_change}",
    )

    add_toast(request, type="success", message=success_message)

    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/dashboard/profile"})
    return RedirectResponse(url="/dashboard/profile", status_code=status.HTTP_303_SEE_OTHER)
