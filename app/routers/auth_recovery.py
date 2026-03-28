from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import get_redis
from app.config import get_settings
from app.db.database import get_db_session
from app.db.models import User
from app.rate_limit import LimitRule, safe_identity
from app.services.audit_service import write_audit_log
from app.services.auth_service import consume_reset_token, create_reset_token, revoke_all_sessions
from app.services.email_service import send_templated_email
from app.services.flash_service import add_toast
from app.templating import templates

from .auth_common import apply_rate_limits, get_ip, redirect_authenticated_user

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

    clean_email = email.lower().strip()
    user = (await db.execute(select(User).where(User.email == clean_email, User.is_active.is_(True)))).scalar_one_or_none()
    if user:
        signed_token, _ = await create_reset_token(db=db, user_id=user.id)
        reset_link = f"{settings.app_url}/reset-password?token={quote_plus(signed_token)}"
        await send_templated_email(
            subject="Reset your password",
            recipients=[user.email],
            template_name="reset_password",
            context={
                "subject": "Reset your password",
                "preheader": "Reset your FastAuth password securely.",
                "action_url": reset_link,
                "expires_hours": 24,
            },
        )

    add_toast(request, type="success", message="If your email exists, a reset link was sent.")
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
    password: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    await apply_rate_limits(redis, [(RESET_RULE, safe_identity(get_ip(request)))])

    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "auth/reset_password.html",
            {"title": "Reset Password", "token": token, "error": "Password must be at least 8 characters."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = await consume_reset_token(db=db, signed_token=token, new_password=password)
    await revoke_all_sessions(db=db, user_id=user.id)
    await write_audit_log(db, action="PASSWORD_RESET", target="user", user_id=user.id, request=request)
    add_toast(request, type="success", message="Password reset complete. Please sign in.")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/login"})
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
