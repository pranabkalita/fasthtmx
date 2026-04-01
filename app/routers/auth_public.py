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
from app.dependencies import get_authenticated_user_from_request, redirect_authenticated_user
from app.db.models import User
from app.schemas import LoginForm, RegistrationForm, ResendVerificationForm, first_validation_error
from app.rate_limit import LimitRule, apply_rate_limits, get_ip, safe_identity
from app.services.deferred_email_service import defer_templated_email
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
from app.services.flash_service import add_toast
from app.services.job_queue import JobEnqueueError, enqueue_templated_email
from app.templating import templates

router = APIRouter(tags=["auth"])
settings = get_settings()

REGISTER_RULE = LimitRule(key_prefix="rl:register:ip", limit=5, window_seconds=60)
LOGIN_IP_RULE = LimitRule(key_prefix="rl:login:ip", limit=20, window_seconds=60)
LOGIN_EMAIL_RULE = LimitRule(key_prefix="rl:login:email", limit=8, window_seconds=60)
RESEND_VERIFY_IP_RULE = LimitRule(key_prefix="rl:verify-resend:ip", limit=5, window_seconds=60)
RESEND_VERIFY_EMAIL_RULE = LimitRule(key_prefix="rl:verify-resend:email", limit=3, window_seconds=600)


def render_login_page(
    request: Request,
    *,
    status_code: int = status.HTTP_200_OK,
    error: str | None = None,
    success: str | None = None,
    email_value: str = "",
    two_factor_code_value: str = "",
) -> HTMLResponse:
    context = {
        "title": "Login",
        "email_value": email_value,
        "two_factor_code_value": two_factor_code_value,
    }
    if error:
        context["error"] = error
    if success:
        context["success"] = success
    return templates.TemplateResponse(
        request,
        "auth/login.html",
        context,
        status_code=status_code,
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
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> HTMLResponse:
    await apply_rate_limits(redis, [(REGISTER_RULE, safe_identity(get_ip(request)))])

    try:
        payload = RegistrationForm.model_validate(
            {
                "email": email,
                "full_name": full_name,
                "password": password,
                "confirm_password": confirm_password,
            }
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "auth/register.html",
            {"title": "Create Account", "error": first_validation_error(exc)},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = await create_user(
        db=db,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
    )
    signed_token, _ = await create_email_verification_token(db=db, user_id=user.id)
    verify_link = f"{settings.app_url}/verify-email?token={quote_plus(signed_token)}"
    registration_message = "Registration complete. Your verification email will arrive shortly."

    try:
        email_job_id = await enqueue_templated_email(
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
            metadata={
                "user_id": user.id,
                "request_id": request.headers.get("x-request-id", ""),
                "route": "register",
            },
        )
    except JobEnqueueError:
        deferred = await defer_templated_email(
            db,
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
            metadata={
                "user_id": user.id,
                "request_id": request.headers.get("x-request-id", ""),
                "route": "register",
            },
            user_id=user.id,
        )
        email_job_id = f"deferred:{deferred.id}"
        registration_message = (
            "Registration complete. Your verification email is delayed and will be retried automatically."
        )
    await write_audit_log(
        db,
        action="REGISTER",
        target="user",
        user_id=user.id,
        request=request,
        details=f"verification_email_job_id={email_job_id}",
    )
    add_toast(request, type="success", message=registration_message)
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

    try:
        payload = ResendVerificationForm.model_validate({"email": email})
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "auth/login.html",
            {
                "title": "Login",
                "error": first_validation_error(exc),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    user = (
        await db.execute(select(User).where(User.email == payload.email, User.is_active.is_(True)))
    ).scalar_one_or_none()
    if user and not user.is_verified:
        signed_token, _ = await create_email_verification_token(db=db, user_id=user.id)
        verify_link = f"{settings.app_url}/verify-email?token={quote_plus(signed_token)}"
        try:
            email_job_id = await enqueue_templated_email(
                subject="Verify your account",
                recipients=[user.email],
                template_name="verify_account_resend",
                context={
                    "subject": "Verify your account",
                    "preheader": "Here is your new FastAuth verification link.",
                    "action_url": verify_link,
                    "expires_hours": 24,
                },
                metadata={
                    "user_id": user.id,
                    "request_id": request.headers.get("x-request-id", ""),
                    "route": "verify_email_resend",
                },
            )
        except JobEnqueueError:
            deferred = await defer_templated_email(
                db,
                subject="Verify your account",
                recipients=[user.email],
                template_name="verify_account_resend",
                context={
                    "subject": "Verify your account",
                    "preheader": "Here is your new FastAuth verification link.",
                    "action_url": verify_link,
                    "expires_hours": 24,
                },
                metadata={
                    "user_id": user.id,
                    "request_id": request.headers.get("x-request-id", ""),
                    "route": "verify_email_resend",
                },
                user_id=user.id,
            )
            email_job_id = f"deferred:{deferred.id}"
        await write_audit_log(
            db,
            action="EMAIL_VERIFICATION_RESEND_QUEUED",
            target="user",
            user_id=user.id,
            request=request,
            details=f"verification_email_job_id={email_job_id}",
        )

    add_toast(
        request,
        type="success",
        message="If the account exists and is unverified, a verification email will arrive shortly.",
    )
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/login"})
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: AsyncSession = Depends(get_db_session)) -> HTMLResponse:
    redirect = await redirect_authenticated_user(request, db)
    if redirect:
        return redirect
    return render_login_page(request)


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
    is_htmx = request.headers.get("HX-Request") == "true"
    await apply_rate_limits(
        redis,
        [
            (LOGIN_IP_RULE, safe_identity(ip)),
            (LOGIN_EMAIL_RULE, safe_identity(clean_email)),
        ],
    )

    try:
        payload = LoginForm.model_validate(
            {
                "email": email,
                "password": password,
                "two_factor_code": two_factor_code,
            }
        )
    except ValidationError:
        return render_login_page(
            request,
            error="Invalid credentials.",
            email_value=email.strip(),
            two_factor_code_value=two_factor_code.strip(),
            status_code=status.HTTP_200_OK if is_htmx else status.HTTP_401_UNAUTHORIZED,
        )

    if await is_locked_out(db, payload.email):
        return render_login_page(
            request,
            error=(
                "Too many failed attempts. Please wait "
                f"{settings.login_lockout_minutes} minutes before trying again."
            ),
            email_value=payload.email,
            two_factor_code_value=payload.two_factor_code,
            status_code=status.HTTP_200_OK if is_htmx else status.HTTP_429_TOO_MANY_REQUESTS,
        )

    user = await authenticate_user(db, payload.email, payload.password)
    if not user:
        await record_login_attempt(db, payload.email, ip, success=False)
        return render_login_page(
            request,
            error="Invalid credentials.",
            email_value=payload.email,
            two_factor_code_value=payload.two_factor_code,
            status_code=status.HTTP_200_OK if is_htmx else status.HTTP_401_UNAUTHORIZED,
        )

    if not user.is_verified:
        return render_login_page(
            request,
            error="Verify your email before signing in.",
            email_value=payload.email,
            two_factor_code_value=payload.two_factor_code,
            status_code=status.HTTP_200_OK if is_htmx else status.HTTP_403_FORBIDDEN,
        )

    if user.two_factor_enabled:
        if not payload.two_factor_code:
            return render_login_page(
                request,
                error="This account has 2FA enabled. Enter your password and then provide a valid authenticator or backup code.",
                email_value=payload.email,
                status_code=status.HTTP_200_OK if is_htmx else status.HTTP_401_UNAUTHORIZED,
            )

        totp_valid = bool(
            user.two_factor_secret and verify_totp(user.two_factor_secret, payload.two_factor_code)
        )
        backup_valid = False
        if not totp_valid:
            backup_valid = await consume_backup_code(db, user.id, payload.two_factor_code)

        if not totp_valid and not backup_valid:
            await record_login_attempt(db, payload.email, ip, success=False)
            return render_login_page(
                request,
                error="The authenticator or backup code is invalid. Please try again with a current 6-digit code or an unused backup code.",
                email_value=payload.email,
                two_factor_code_value=payload.two_factor_code,
                status_code=status.HTTP_200_OK if is_htmx else status.HTTP_401_UNAUTHORIZED,
            )

    await record_login_attempt(db, payload.email, ip, success=True)
    session_token = await create_session(
        db=db,
        user_id=user.id,
        ip_address=ip,
        user_agent=request.headers.get("user-agent", ""),
    )
    await write_audit_log(db, action="LOGIN", target="session", user_id=user.id, request=request)
    add_toast(request, type="success", message="Welcome back.")

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
