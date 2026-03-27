from __future__ import annotations

import base64
import io
from datetime import UTC, datetime
from urllib.parse import quote_plus

import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.templating import Jinja2Templates

from app.cache import get_redis
from app.config import get_settings
from app.db.database import get_db_session
from app.db.models import Session, User
from app.dependencies import get_admin_user, get_current_user
from app.rate_limit import LimitRule, RateLimiter, safe_identity
from app.security import hash_password, hash_token, verify_password
from app.services.audit_service import write_audit_log
from app.services.auth_service import (
    authenticate_user,
    build_totp_uri,
    consume_backup_code,
    consume_reset_token,
    create_email_verification_token,
    create_reset_token,
    create_session,
    create_user,
    reset_backup_codes,
    is_locked_out,
    record_login_attempt,
    revoke_session_by_id,
    revoke_all_sessions,
    revoke_session,
    verify_email_token,
    verify_totp,
)
from app.services.email_service import render_email_bodies, send_templated_email

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")
settings = get_settings()

REGISTER_RULE = LimitRule(key_prefix="rl:register:ip", limit=5, window_seconds=60)
LOGIN_IP_RULE = LimitRule(key_prefix="rl:login:ip", limit=20, window_seconds=60)
LOGIN_EMAIL_RULE = LimitRule(key_prefix="rl:login:email", limit=8, window_seconds=60)
FORGOT_RULE = LimitRule(key_prefix="rl:forgot:ip", limit=5, window_seconds=60)
RESET_RULE = LimitRule(key_prefix="rl:reset:ip", limit=8, window_seconds=60)
RESEND_VERIFY_IP_RULE = LimitRule(key_prefix="rl:verify-resend:ip", limit=5, window_seconds=60)
RESEND_VERIFY_EMAIL_RULE = LimitRule(key_prefix="rl:verify-resend:email", limit=3, window_seconds=600)

EMAIL_PREVIEW_TEMPLATES = (
    "verify_account",
    "verify_account_resend",
    "reset_password",
    "verify_new_email",
)


def get_ip(request: Request) -> str:
    if request.client:
        return request.client.host
    return "unknown"


async def apply_rate_limits(redis: Redis, rules_and_ids: list[tuple[LimitRule, str]]) -> None:
    limiter = RateLimiter(redis)
    for rule, identity in rules_and_ids:
        await limiter.hit(rule, identity)


async def get_authenticated_user_from_request(request: Request, db: AsyncSession) -> User | None:
    raw_session = request.cookies.get(settings.session_cookie_name)
    if not raw_session:
        return None

    token_hash = hash_token(raw_session)
    now = datetime.now(UTC).replace(tzinfo=None)
    session_row = (
        await db.execute(
            select(Session).where(
                Session.token_hash == token_hash,
                Session.expires_at >= now,
            )
        )
    ).scalar_one_or_none()
    if not session_row:
        return None

    user = (
        await db.execute(
            select(User).where(
                User.id == session_row.user_id,
                User.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    return user


async def redirect_authenticated_user(request: Request, db: AsyncSession) -> RedirectResponse | None:
    user = await get_authenticated_user_from_request(request, db)
    if user:
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    return None


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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, db: AsyncSession = Depends(get_db_session)) -> HTMLResponse:
    auth_user = await get_authenticated_user_from_request(request, db)
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "title": "Welcome",
            "auth_user": auth_user,
        },
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    redirect = await redirect_authenticated_user(request, db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "auth/register.html", {"title": "Create Account"})


@router.post("/register", response_class=HTMLResponse)
async def register(
    request: Request,
    email: str = Form(...),
    full_name: str = Form(""),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    await apply_rate_limits(redis, [(REGISTER_RULE, safe_identity(get_ip(request)))])

    if len(password) < 8:
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {"title": "Create Account", "error": "Password must be at least 8 characters."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = await create_user(db=db, email=email.lower().strip(), password=password, full_name=full_name.strip())
    signed_token, _ = await create_email_verification_token(db=db, user_id=user.id)
    verify_link = f"{settings.app_url}/verify-email?token={quote_plus(signed_token)}"

    await send_templated_email(
        subject="Verify your account",
        recipients=[user.email],
        template_name="verify_account",
        context={
            "subject": "Verify your account",
            "preheader": "Confirm your email to activate your FastAuth account.",
            "user_name": user.full_name or "",
            "action_url": verify_link,
            "expires_hours": 24,
        },
    )
    await write_audit_log(db, action="REGISTER", target="user", user_id=user.id, request=request)
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"title": "Login", "success": "Registration complete. Check your email to verify your account."},
    )


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    user = await verify_email_token(db=db, signed_token=token)
    await write_audit_log(db, action="EMAIL_VERIFIED", target="user", user_id=user.id, request=request)
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"title": "Login", "success": "Email verified. You can now sign in."},
    )


@router.post("/verify-email/resend", response_class=HTMLResponse)
async def resend_verification(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    clean_email = email.lower().strip()
    await apply_rate_limits(
        redis,
        [
            (RESEND_VERIFY_IP_RULE, safe_identity(get_ip(request))),
            (RESEND_VERIFY_EMAIL_RULE, safe_identity(clean_email)),
        ],
    )

    user = (
        await db.execute(select(User).where(User.email == clean_email, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if user and not user.is_verified:
        signed_token, _ = await create_email_verification_token(db=db, user_id=user.id)
        verify_link = f"{settings.app_url}/verify-email?token={quote_plus(signed_token)}"
        await send_templated_email(
            subject="Verify your account",
            recipients=[user.email],
            template_name="verify_account_resend",
            context={
                "subject": "Verify your account",
                "preheader": "Here is your new FastAuth verification link.",
                "action_url": verify_link,
                "expires_hours": 24,
            },
        )

    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {
            "title": "Login",
            "success": "If the account exists and is unverified, a verification email was sent.",
        },
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db_session)) -> HTMLResponse:
    redirect = await redirect_authenticated_user(request, db)
    if redirect:
        return redirect
    return templates.TemplateResponse(request, "auth/login.html", {"title": "Login"})


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    two_factor_code: str = Form(""),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    clean_email = email.lower().strip()
    ip = get_ip(request)
    await apply_rate_limits(
        redis,
        [
            (LOGIN_IP_RULE, safe_identity(ip)),
            (LOGIN_EMAIL_RULE, safe_identity(clean_email)),
        ],
    )

    if await is_locked_out(db, clean_email):
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {
                "title": "Login",
                "error": (
                    "Too many failed attempts. Please wait "
                    f"{settings.login_lockout_minutes} minutes before trying again."
                ),
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user = await authenticate_user(db, clean_email, password)
    if not user:
        await record_login_attempt(db, clean_email, ip, success=False)
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"title": "Login", "error": "Invalid credentials."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_verified:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {"title": "Login", "error": "Verify your email before signing in."},
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if user.two_factor_enabled:
        if not two_factor_code:
            return templates.TemplateResponse(
                request,
                "auth/login.html",
                {
                    "title": "Login",
                    "error": "This account has 2FA enabled. Enter your password and then provide a valid authenticator or backup code.",
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        totp_valid = bool(user.two_factor_secret and verify_totp(user.two_factor_secret, two_factor_code))
        backup_valid = False
        if not totp_valid:
            backup_valid = await consume_backup_code(db, user.id, two_factor_code)

        if not totp_valid and not backup_valid:
            await record_login_attempt(db, clean_email, ip, success=False)
            return templates.TemplateResponse(
                request,
                "auth/login.html",
                {
                    "title": "Login",
                    "error": "The authenticator or backup code is invalid. Please try again with a current 6-digit code or an unused backup code.",
                },
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

    await record_login_attempt(db, clean_email, ip, success=True)
    session_token = await create_session(
        db=db,
        user_id=user.id,
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )
    await write_audit_log(db, action="LOGIN", target="session", user_id=user.id, request=request)

    redirect = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=settings.session_max_age,
    )
    return redirect


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    raw_session = request.cookies.get(settings.session_cookie_name)
    if raw_session:
        await revoke_session(db, raw_session)

    await write_audit_log(db, action="LOGOUT", target="session", user_id=current_user.id, request=request)
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect


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

    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"title": "Login", "success": "If your email exists, a reset link was sent."},
    )


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
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        {"title": "Login", "success": "Password reset complete. Please sign in."},
    )


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    current_session_id = None
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    if raw_cookie:
        current_hash = hash_token(raw_cookie)
        current_session = (
            await db.execute(
                select(Session).where(
                    Session.user_id == current_user.id,
                    Session.token_hash == current_hash,
                )
            )
        ).scalar_one_or_none()
        if current_session:
            current_session_id = current_session.id

    sessions = (
        await db.execute(
            select(Session)
            .where(Session.user_id == current_user.id, Session.expires_at >= datetime.now(UTC))
            .order_by(Session.created_at.desc())
        )
    ).scalars().all()
    return templates.TemplateResponse(
        request,
        "dashboard/sessions.html",
        {
            "title": "Sessions",
            "user": current_user,
            "sessions": sessions,
            "current_session_id": current_session_id,
        },
    )


@router.post("/sessions/{session_id}/revoke")
async def revoke_single_session(
    request: Request,
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    is_current_session = False
    raw_cookie = request.cookies.get(settings.session_cookie_name)
    if raw_cookie:
        current_hash = hash_token(raw_cookie)
        current_session = (
            await db.execute(
                select(Session).where(
                    Session.user_id == current_user.id,
                    Session.token_hash == current_hash,
                )
            )
        ).scalar_one_or_none()
        is_current_session = bool(current_session and current_session.id == session_id)

    await revoke_session_by_id(db, current_user.id, session_id)

    if is_current_session:
        redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        redirect.delete_cookie(settings.session_cookie_name)
        return redirect

    return RedirectResponse(url="/sessions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sessions/logout-all")
async def logout_all_devices(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    await revoke_all_sessions(db, current_user.id)
    await write_audit_log(db, action="LOGOUT_ALL", target="session", user_id=current_user.id, request=request)

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect


@router.get("/2fa/setup", response_class=HTMLResponse)
async def setup_2fa(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    secret, uri = build_totp_uri(current_user)

    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")

    return templates.TemplateResponse(
        request,
        "dashboard/two_factor.html",
        {
            "title": "2FA Setup",
            "user": current_user,
            "secret": secret,
            "qr_data": encoded,
        },
    )


@router.post("/2fa/enable")
async def enable_2fa(
    request: Request,
    secret: str = Form(...),
    code: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    if not verify_totp(secret, code):
        return RedirectResponse(url="/2fa/setup", status_code=status.HTTP_303_SEE_OTHER)

    current_user.two_factor_enabled = True
    current_user.two_factor_secret = secret
    await db.commit()
    backup_codes = await reset_backup_codes(db, current_user.id)
    await write_audit_log(db, action="2FA_ENABLED", target="user", user_id=current_user.id, request=request)
    return templates.TemplateResponse(
        request,
        "dashboard/backup_codes.html",
        {
            "title": "Backup Codes",
            "user": current_user,
            "backup_codes": backup_codes,
            "success": "2FA enabled. Save these backup codes in a safe place.",
        },
    )


@router.post("/2fa/disable")
async def disable_2fa(
    request: Request,
    password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    if not verify_password(password, current_user.password_hash):
        return RedirectResponse(url="/dashboard/profile", status_code=status.HTTP_303_SEE_OTHER)

    current_user.two_factor_enabled = False
    current_user.two_factor_secret = None
    await db.commit()
    await write_audit_log(db, action="2FA_DISABLED", target="user", user_id=current_user.id, request=request)
    return RedirectResponse(url="/dashboard/profile", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/password/change", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    if not verify_password(current_password, current_user.password_hash):
        return templates.TemplateResponse(
            request,
            "dashboard/profile.html",
            {"title": "Profile", "user": current_user, "error": "Current password is invalid."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if len(new_password) < 8:
        return templates.TemplateResponse(
            request,
            "dashboard/profile.html",
            {"title": "Profile", "user": current_user, "error": "New password must be at least 8 characters."},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    current_user.password_hash = hash_password(new_password)
    await db.commit()
    await revoke_all_sessions(db, current_user.id)
    await write_audit_log(db, action="PASSWORD_CHANGED", target="user", user_id=current_user.id, request=request)

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect


@router.post("/account/deactivate")
async def deactivate_account(
    request: Request,
    password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    if not verify_password(password, current_user.password_hash):
        return RedirectResponse(url="/dashboard/profile", status_code=status.HTTP_303_SEE_OTHER)

    current_user.is_active = False
    current_user.deleted_at = datetime.now(UTC)
    await db.commit()
    await revoke_all_sessions(db, current_user.id)
    await write_audit_log(db, action="ACCOUNT_DEACTIVATED", target="user", user_id=current_user.id, request=request)

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect
