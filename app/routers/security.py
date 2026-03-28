from __future__ import annotations

import base64
import io
from datetime import UTC, datetime

import qrcode
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db_session
from app.db.models import Session, User
from app.dependencies import get_current_user
from app.security import hash_password, hash_token, verify_password
from app.services.audit_service import write_audit_log
from app.services.auth_service import (
    build_totp_uri,
    reset_backup_codes,
    revoke_all_sessions,
    revoke_session,
    revoke_session_by_id,
    verify_totp,
)
from app.services.flash_service import add_toast
from app.templating import templates

router = APIRouter(tags=["security"])
settings = get_settings()


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
    add_toast(request, type="success", message="You have been logged out.")
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect


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
        add_toast(request, type="success", message="Current session revoked. Please sign in again.")
        redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        redirect.delete_cookie(settings.session_cookie_name)
        return redirect

    add_toast(request, type="success", message="Session revoked.")
    return RedirectResponse(url="/sessions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/sessions/logout-all")
async def logout_all_devices(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    await revoke_all_sessions(db, current_user.id)
    await write_audit_log(db, action="LOGOUT_ALL", target="session", user_id=current_user.id, request=request)

    add_toast(request, type="success", message="All sessions were signed out.")
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


@router.get("/2fa/backup-codes", response_class=HTMLResponse)
async def backup_codes_page(
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
            return HTMLResponse("", headers={"HX-Redirect": "/dashboard/profile"})
        return RedirectResponse(url="/dashboard/profile", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(
        request,
        "dashboard/backup_codes.html",
        {
            "title": "Backup Codes",
            "user": current_user,
            "backup_codes": backup_codes,
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
        add_toast(request, type="error", message="Invalid code. Enter a current authenticator code.")
        if request.headers.get("HX-Request") == "true":
            return HTMLResponse("", headers={"HX-Redirect": "/2fa/setup"})
        return RedirectResponse(url="/2fa/setup", status_code=status.HTTP_303_SEE_OTHER)

    current_user.two_factor_enabled = True
    current_user.two_factor_secret = secret
    await db.commit()
    backup_codes = await reset_backup_codes(db, current_user.id)
    await write_audit_log(db, action="2FA_ENABLED", target="user", user_id=current_user.id, request=request)
    request.session["_backup_codes_once"] = {
        "user_id": current_user.id,
        "codes": backup_codes,
    }
    add_toast(request, type="success", message="2FA enabled. Save these backup codes in a safe place.")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/2fa/backup-codes"})
    return RedirectResponse(url="/2fa/backup-codes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/2fa/disable")
async def disable_2fa(
    request: Request,
    password: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> HTMLResponse:
    if not verify_password(password, current_user.password_hash):
        return templates.TemplateResponse(
            request,
            "dashboard/profile.html",
            {
                "title": "Profile",
                "user": current_user,
                "error": "Password is incorrect. 2FA was not disabled.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    current_user.two_factor_enabled = False
    current_user.two_factor_secret = None
    await db.commit()
    await write_audit_log(db, action="2FA_DISABLED", target="user", user_id=current_user.id, request=request)
    add_toast(request, type="success", message="2FA has been disabled for your account.")
    if request.headers.get("HX-Request") == "true":
        return HTMLResponse("", headers={"HX-Redirect": "/dashboard/profile"})
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

    add_toast(request, type="success", message="Password changed. Please sign in again.")
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
        return templates.TemplateResponse(
            request,
            "dashboard/profile.html",
            {
                "title": "Profile",
                "user": current_user,
                "error": "Password is incorrect. Account was not deactivated.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    current_user.is_active = False
    current_user.deleted_at = datetime.now(UTC)
    await db.commit()
    await revoke_all_sessions(db, current_user.id)
    await write_audit_log(db, action="ACCOUNT_DEACTIVATED", target="user", user_id=current_user.id, request=request)

    add_toast(request, type="success", message="Account deactivated successfully.")
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect
