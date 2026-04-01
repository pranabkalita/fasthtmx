import base64
import io
from datetime import UTC, datetime
from urllib.parse import quote_plus

import qrcode
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db_session
from app.db.models import Session, User
from app.dependencies import get_current_session, get_current_user
from app.schemas import (
    ChangePasswordForm,
    DeactivateAccountForm,
    DisableTwoFactorForm,
    EnableTwoFactorForm,
    ProfileUpdateForm,
    first_validation_error,
)
from app.security import hash_password, verify_password
from app.services.auth_service import (
    build_totp_uri,
    create_email_verification_token,
    get_user_totp_secret,
    mark_session_step_up_verified,
    reset_backup_codes,
    revoke_all_sessions,
    session_step_up_is_fresh,
    set_user_totp_secret,
    verify_totp,
)
from app.services.audit_service import write_audit_log
from app.services.deferred_email_service import defer_templated_email
from app.services.flash_service import add_toast
from app.services.job_queue import JobEnqueueError, enqueue_templated_email
from app.templating import templates

router = APIRouter(tags=["profile"])
settings = get_settings()


@router.get("/profile/change-password", response_class=HTMLResponse)
async def profile_change_password(
    request: Request, current_user: User = Depends(get_current_user)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profile/change_password.html",
        {
            "title": "Change password",
            "user": current_user,
            "profile_section": "password",
        },
    )


@router.get("/profile/2fa", response_class=HTMLResponse)
async def profile_two_factor_settings(
    request: Request, current_user: User = Depends(get_current_user)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profile/2fa_settings.html",
        {
            "title": "Two-factor authentication",
            "user": current_user,
            "two_factor_secret_available": bool(get_user_totp_secret(current_user)),
            "profile_section": "two_factor",
        },
    )


@router.get("/profile/2fa/setup", response_class=HTMLResponse)
async def profile_two_factor_setup(
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
        "profile/two_factor.html",
        {
            "title": "2FA Setup",
            "user": current_user,
            "secret": secret,
            "qr_data": encoded,
        },
    )


@router.get("/profile/2fa/backup-codes", response_class=HTMLResponse)
async def profile_two_factor_backup_codes(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> HTMLResponse:
    payload = request.session.pop("_backup_codes_once", None)
    backup_codes: list[str] = []

    if isinstance(payload, dict) and payload.get("user_id") == current_user.id:
        raw_codes = payload.get("codes", [])
        if isinstance(raw_codes, list):
            backup_codes = [str(code) for code in raw_codes if code]

    if not backup_codes:
        add_toast(
            request,
            type="error",
            message="Backup codes are available only right after enabling 2FA.",
        )
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse("", headers={"HX-Redirect": "/profile/2fa"})
        return RedirectResponse(url="/profile/2fa", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request,
        "profile/backup_codes.html",
        {
            "title": "Backup Codes",
            "user": current_user,
            "backup_codes": backup_codes,
        },
    )


@router.get("/profile/deactivate-account", response_class=HTMLResponse)
async def profile_deactivate_account(
    request: Request, current_user: User = Depends(get_current_user)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profile/deactivate_account.html",
        {
            "title": "Deactivate account",
            "user": current_user,
            "profile_section": "deactivate",
        },
    )


@router.get("/profile", response_class=HTMLResponse)
async def profile(request: Request, current_user: User = Depends(get_current_user)) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profile/index.html",
        {
            "title": "Profile",
            "user": current_user,
            "profile_section": "identity",
        },
    )


@router.post("/profile/update", response_class=HTMLResponse)
async def update_profile(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(...),
    current_user: User = Depends(get_current_user),
    current_session: Session = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    try:
        payload = ProfileUpdateForm.model_validate(
            {
                "full_name": full_name,
                "email": email,
            }
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "profile/index.html",
            {
                "title": "Profile",
                "user": current_user,
                "profile_section": "identity",
                "error": first_validation_error(exc),
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if payload.email != current_user.email:
        existing = (
            await db.execute(
                select(User).where(User.email == payload.email, User.id != current_user.id)
            )
        ).scalar_one_or_none()
        if existing:
            return templates.TemplateResponse(
                request,
                "profile/index.html",
                {
                    "title": "Profile",
                    "user": current_user,
                    "profile_section": "identity",
                    "error": "That email is already used by another account.",
                },
                status_code=status.HTTP_409_CONFLICT,
            )

    has_email_change = payload.email != current_user.email
    if has_email_change and not session_step_up_is_fresh(current_session):
        return templates.TemplateResponse(
            request,
            "profile/index.html",
            {
                "title": "Profile",
                "user": current_user,
                "profile_section": "identity",
                "error": "For email changes, please re-authenticate recently and try again.",
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )
    current_user.full_name = payload.full_name
    current_user.email = payload.email
    success_message = "Profile updated successfully."

    if has_email_change:
        current_user.is_verified = False

    await db.commit()
    await db.refresh(current_user)

    if has_email_change:
        signed_token, _ = await create_email_verification_token(db=db, user_id=current_user.id)
        verify_link = f"{settings.app_url}/verify-email?token={quote_plus(signed_token)}"
        email_job_id = ""
        try:
            email_job_id = await enqueue_templated_email(
                subject="Verify your new email address",
                recipients=[current_user.email],
                template_name="verify_new_email",
                context={
                    "subject": "Verify your new email address",
                    "preheader": "Confirm your new email to keep account access.",
                    "action_url": verify_link,
                    "expires_hours": 24,
                },
                metadata={
                    "user_id": current_user.id,
                    "request_id": request.headers.get("x-request-id", ""),
                    "route": "profile_update",
                },
            )
        except JobEnqueueError:
            deferred = await defer_templated_email(
                db,
                subject="Verify your new email address",
                recipients=[current_user.email],
                template_name="verify_new_email",
                context={
                    "subject": "Verify your new email address",
                    "preheader": "Confirm your new email to keep account access.",
                    "action_url": verify_link,
                    "expires_hours": 24,
                },
                metadata={
                    "user_id": current_user.id,
                    "request_id": request.headers.get("x-request-id", ""),
                    "route": "profile_update",
                },
                user_id=current_user.id,
            )
            email_job_id = f"deferred:{deferred.id}"
            success_message = (
                "Profile updated. Your verification email is delayed and will be retried automatically."
            )
        else:
            success_message = "Profile updated. Your verification email will arrive shortly."

    await write_audit_log(
        db,
        action="PROFILE_UPDATED_EMAIL_PENDING_VERIFY" if has_email_change else "PROFILE_UPDATED",
        target="user",
        user_id=current_user.id,
        request=request,
        details=(
            f"email_changed={has_email_change};verification_email_job_id={email_job_id}"
            if has_email_change
            else f"email_changed={has_email_change}"
        ),
    )

    add_toast(request, type="success", message=success_message)

    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/profile"})
    return RedirectResponse(url="/profile", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/profile/2fa/enable")
async def enable_2fa(
    request: Request,
    secret: str = Form(...),
    code: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    try:
        payload = EnableTwoFactorForm.model_validate({"secret": secret, "code": code})
    except ValidationError:
        add_toast(request, type="error", message="Invalid code. Enter a current authenticator code.")
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse("", headers={"HX-Redirect": "/profile/2fa/setup"})
        return RedirectResponse(url="/profile/2fa/setup", status_code=status.HTTP_303_SEE_OTHER)

    if not verify_totp(payload.secret, payload.code):
        add_toast(request, type="error", message="Invalid code. Enter a current authenticator code.")
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse("", headers={"HX-Redirect": "/profile/2fa/setup"})
        return RedirectResponse(url="/profile/2fa/setup", status_code=status.HTTP_303_SEE_OTHER)

    current_user.two_factor_enabled = True
    set_user_totp_secret(current_user, payload.secret)
    await db.commit()
    backup_codes = await reset_backup_codes(db, current_user.id)
    await write_audit_log(db, action="2FA_ENABLED", target="user", user_id=current_user.id, request=request)
    request.session["_backup_codes_once"] = {
        "user_id": current_user.id,
        "codes": backup_codes,
    }
    add_toast(request, type="success", message="2FA enabled. Save these backup codes in a safe place.")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/profile/2fa/backup-codes"})
    return RedirectResponse(url="/profile/2fa/backup-codes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/profile/2fa/disable")
async def disable_2fa(
    request: Request,
    password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    try:
        payload = DisableTwoFactorForm.model_validate({"password": password})
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "profile/2fa_settings.html",
            {
                "title": "Two-factor authentication",
                "user": current_user,
                "profile_section": "two_factor",
                "error": "Password is incorrect. 2FA was not disabled.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not verify_password(payload.password, current_user.password_hash):
        return templates.TemplateResponse(
            request,
            "profile/2fa_settings.html",
            {
                "title": "Two-factor authentication",
                "user": current_user,
                "profile_section": "two_factor",
                "error": "Password is incorrect. 2FA was not disabled.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    current_user.two_factor_enabled = False
    mark_session_step_up_verified(await get_current_session(request, db))
    set_user_totp_secret(current_user, None)
    await db.commit()
    await write_audit_log(db, action="2FA_DISABLED", target="user", user_id=current_user.id, request=request)
    add_toast(request, type="success", message="2FA has been disabled for your account.")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/profile/2fa"})
    return RedirectResponse(url="/profile/2fa", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/profile/change-password", response_class=HTMLResponse)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_new_password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    error_status_code = (
        status.HTTP_200_OK
        if request.headers.get("HX-Request") == "true"
        else status.HTTP_400_BAD_REQUEST
    )

    try:
        payload = ChangePasswordForm.model_validate(
            {
                "current_password": current_password,
                "new_password": new_password,
                "confirm_new_password": confirm_new_password,
            }
        )
    except ValidationError as exc:
        return templates.TemplateResponse(
            request,
            "profile/change_password.html",
            {
                "title": "Change password",
                "user": current_user,
                "profile_section": "password",
                "change_password_error": first_validation_error(exc),
            },
            status_code=error_status_code,
        )

    if not verify_password(payload.current_password, current_user.password_hash):
        return templates.TemplateResponse(
            request,
            "profile/change_password.html",
            {
                "title": "Change password",
                "user": current_user,
                "profile_section": "password",
                "change_password_error": "Current password is invalid.",
            },
            status_code=error_status_code,
        )

    mark_session_step_up_verified(await get_current_session(request, db))
    current_user.password_hash = hash_password(payload.new_password)
    await db.commit()
    await revoke_all_sessions(db, current_user.id)
    await write_audit_log(db, action="PASSWORD_CHANGED", target="user", user_id=current_user.id, request=request)

    add_toast(request, type="success", message="Password changed. Please sign in again.")
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect


@router.post("/profile/deactivate-account")
async def deactivate_account(
    request: Request,
    password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    try:
        payload = DeactivateAccountForm.model_validate({"password": password})
    except ValidationError:
        return templates.TemplateResponse(
            request,
            "profile/deactivate_account.html",
            {
                "title": "Deactivate account",
                "user": current_user,
                "profile_section": "deactivate",
                "error": "Password is incorrect. Account was not deactivated.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if not verify_password(payload.password, current_user.password_hash):
        return templates.TemplateResponse(
            request,
            "profile/deactivate_account.html",
            {
                "title": "Deactivate account",
                "user": current_user,
                "profile_section": "deactivate",
                "error": "Password is incorrect. Account was not deactivated.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    mark_session_step_up_verified(await get_current_session(request, db))
    current_user.is_active = False
    current_user.deleted_at = datetime.now(UTC)
    await db.commit()
    await revoke_all_sessions(db, current_user.id)
    await write_audit_log(db, action="ACCOUNT_DEACTIVATED", target="user", user_id=current_user.id, request=request)

    add_toast(request, type="success", message="Account deactivated successfully.")
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect
