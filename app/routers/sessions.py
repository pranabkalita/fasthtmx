from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db_session
from app.db.models import Session, User
from app.dependencies import get_current_user, require_recent_step_up
from app.security import hash_token
from app.services.audit_service import write_audit_log
from app.services.auth_service import revoke_all_sessions, revoke_session, revoke_session_by_id
from app.services.flash_service import add_toast
from app.templating import templates

router = APIRouter(tags=["sessions"])
settings = get_settings()


@router.post("/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    raw_session = request.cookies.get(settings.session_cookie_name)
    if raw_session:
        await revoke_session(db, raw_session)

    await write_audit_log(db, action="LOGOUT", target="session", user_id=current_user.id, request=request)
    add_toast(request, type="success", message="You have been logged out.")

    if request.headers.get("HX-Request") == "true":
        response = HTMLResponse("", headers={"HX-Redirect": "/login"})
        response.delete_cookie(settings.session_cookie_name)
        return response

    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect


@router.get("/profile/sessions", response_class=HTMLResponse)
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
            .where(
                and_(
                    Session.user_id == current_user.id,
                    Session.expires_at >= datetime.now(UTC),
                    Session.absolute_expires_at >= datetime.now(UTC),
                )
            )
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
            "profile_section": "sessions",
        },
    )


@router.post("/profile/sessions/{session_id}/revoke")
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
    return RedirectResponse(url="/profile/sessions", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/profile/sessions/logout-all")
async def logout_all_devices(
    request: Request,
    current_user: User = Depends(get_current_user),
    _: Session = Depends(require_recent_step_up),
    db: AsyncSession = Depends(get_db_session),
) -> RedirectResponse:
    await revoke_all_sessions(db, current_user.id)
    await write_audit_log(db, action="LOGOUT_ALL", target="session", user_id=current_user.id, request=request)

    add_toast(request, type="success", message="All sessions were signed out.")
    redirect = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    redirect.delete_cookie(settings.session_cookie_name)
    return redirect
