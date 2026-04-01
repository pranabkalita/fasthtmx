from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import get_redis
from app.config import get_settings
from app.db.database import get_db_session
from app.dependencies import redirect_authenticated_user
from app.db.models import User
from app.rate_limit import LimitRule, apply_rate_limits, get_ip, safe_identity
from app.schemas import ForgotPasswordForm, ResetPasswordForm, first_validation_error
from app.services.audit_service import write_audit_log
from app.services.deferred_email_service import defer_templated_email
from app.services.auth_service import consume_reset_token, create_reset_token, revoke_all_sessions
from app.services.flash_service import add_toast
from app.services.job_queue import JobEnqueueError, enqueue_templated_email
from app.templating import templates

router = APIRouter(tags=["auth"])
settings = get_settings()

FORGOT_RULE = LimitRule(key_prefix="rl:forgot:ip", limit=5, window_seconds=60)
RESET_RULE = LimitRule(key_prefix="rl:reset:ip", limit=8, window_seconds=60)


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    redirect = await redirect_authenticated_user(request, db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "auth/forgot_password.html", {"title": "Forgot Password"})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    await apply_rate_limits(redis, [(FORGOT_RULE, safe_identity(get_ip(request)))])

    try:
        payload = ForgotPasswordForm.model_validate({"email": email})
    except ValidationError:
        add_toast(request, type="success", message="If your email exists, a reset link will arrive shortly.")
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse("", headers={"HX-Redirect": "/login"})
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    user = (
        await db.execute(select(User).where(User.email == payload.email, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if user:
        signed_token, _ = await create_reset_token(db=db, user_id=user.id)
        reset_link = f"{settings.app_url}/reset-password?token={quote_plus(signed_token)}"
        try:
            email_job_id = await enqueue_templated_email(
                subject="Reset your password",
                recipients=[user.email],
                template_name="reset_password",
                context={
                    "subject": "Reset your password",
                    "preheader": "Reset your FastAuth password securely.",
                    "action_url": reset_link,
                    "expires_minutes": settings.reset_token_expiry_minutes,
                },
                metadata={
                    "user_id": user.id,
                    "request_id": request.headers.get("x-request-id", ""),
                    "route": "forgot_password",
                },
            )
        except JobEnqueueError:
            deferred = await defer_templated_email(
                db,
                subject="Reset your password",
                recipients=[user.email],
                template_name="reset_password",
                context={
                    "subject": "Reset your password",
                    "preheader": "Reset your FastAuth password securely.",
                    "action_url": reset_link,
                    "expires_minutes": settings.reset_token_expiry_minutes,
                },
                metadata={
                    "user_id": user.id,
                    "request_id": request.headers.get("x-request-id", ""),
                    "route": "forgot_password",
                },
                user_id=user.id,
            )
            email_job_id = f"deferred:{deferred.id}"
        await write_audit_log(
            db,
            action="PASSWORD_RESET_EMAIL_QUEUED",
            target="user",
            user_id=user.id,
            request=request,
            details=f"reset_email_job_id={email_job_id}",
        )

    add_toast(request, type="success", message="If your email exists, a reset link will arrive shortly.")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/login"})
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    redirect = await redirect_authenticated_user(request, db)
    if redirect:
        return redirect
    return templates.TemplateResponse(
        request,
        "auth/reset_password.html",
        {"title": "Reset Password", "token": token},
    )


@router.post("/reset-password", response_class=HTMLResponse)
async def reset_password(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_new_password: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    await apply_rate_limits(redis, [(RESET_RULE, safe_identity(get_ip(request)))])

    error_status_code = (
        status.HTTP_200_OK
        if request.headers.get("HX-Request") == "true"
        else status.HTTP_400_BAD_REQUEST
    )

    try:
        payload = ResetPasswordForm.model_validate(
            {
                "token": token,
                "new_password": new_password,
                "confirm_new_password": confirm_new_password,
            }
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "auth/reset_password.html",
            {
                "title": "Reset Password",
                "token": token,
                "reset_password_error": first_validation_error(exc),
            },
            status_code=error_status_code,
        )

    user = await consume_reset_token(
        db=db,
        signed_token=payload.token,
        new_password=payload.new_password,
    )
    await revoke_all_sessions(db=db, user_id=user.id)
    await write_audit_log(db, action="PASSWORD_RESET", target="user", user_id=user.id, request=request)
    add_toast(request, type="success", message="Password reset complete. Please sign in.")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/login"})
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
