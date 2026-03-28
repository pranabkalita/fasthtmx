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
from app.services.auth_service import (
    authenticate_user,
    consume_backup_code,
    create_email_verification_token,
    create_session,
    create_user,
    is_locked_out,
    record_login_attempt,
    verify_email_token,
    verify_totp,
)
from app.services.email_service import send_templated_email
from app.services.flash_service import add_toast
from app.templating import templates

from .auth_common import (
    apply_rate_limits,
    get_authenticated_user_from_request,
    get_ip,
    redirect_authenticated_user,
)

router = APIRouter(tags=["auth"])
settings = get_settings()

REGISTER_RULE = LimitRule(key_prefix="rl:register:ip", limit=5, window_seconds=60)
LOGIN_IP_RULE = LimitRule(key_prefix="rl:login:ip", limit=20, window_seconds=60)
LOGIN_EMAIL_RULE = LimitRule(key_prefix="rl:login:email", limit=8, window_seconds=60)
RESEND_VERIFY_IP_RULE = LimitRule(key_prefix="rl:verify-resend:ip", limit=5, window_seconds=60)
RESEND_VERIFY_EMAIL_RULE = LimitRule(key_prefix="rl:verify-resend:email", limit=3, window_seconds=600)


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
    add_toast(request, type="success", message="Registration complete. Check your email to verify your account.")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/login"})
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


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

    add_toast(
        request,
        type="success",
        message="If the account exists and is unverified, a verification email was sent.",
    )
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/login"})
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


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
    add_toast(request, type="success", message="Welcome back.")

    is_htmx = request.headers.get("HX-Request") == "true"
    response = (
        HTMLResponse("", headers={"HX-Redirect": "/dashboard"})
        if is_htmx
        else RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_token,
        httponly=True,
        secure=not settings.debug,
        samesite="lax",
        max_age=settings.session_max_age,
    )
    return response
