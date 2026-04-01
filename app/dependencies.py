from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db_session
from app.db.models import Session, User
from app.security import hash_token
from app.services.audit_service import write_audit_log
from app.services.auth_service import (
    renew_session_expiry,
    session_is_absolute_expired,
    session_is_idle_expired,
    session_step_up_is_fresh,
    should_renew_session,
)

settings = get_settings()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    token_hash = hash_token(raw_token)
    session_query = select(Session).where(Session.token_hash == token_hash)
    session_row = (await db.execute(session_query)).scalar_one_or_none()

    if not session_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if session_is_absolute_expired(session_row):
        await write_audit_log(
            db,
            action="SESSION_EXPIRED_ABSOLUTE",
            target="session",
            request=request,
            user_id=session_row.user_id,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if session_is_idle_expired(session_row):
        await write_audit_log(
            db,
            action="SESSION_EXPIRED_IDLE",
            target="session",
            request=request,
            user_id=session_row.user_id,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if should_renew_session(session_row):
        renew_session_expiry(session_row)
        await db.commit()

    user_query = select(User).where(User.id == session_row.user_id, User.is_active.is_(True))
    user = (await db.execute(user_query)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user


async def get_authenticated_user_from_request(request: Request, db: AsyncSession) -> User | None:
    raw_session = request.cookies.get(settings.session_cookie_name)
    if not raw_session:
        return None

    token_hash = hash_token(raw_session)
    session_row = (
        await db.execute(
            select(Session).where(
                Session.token_hash == token_hash,
            )
        )
    ).scalar_one_or_none()
    if not session_row:
        return None
    if session_is_absolute_expired(session_row) or session_is_idle_expired(session_row):
        return None
    if should_renew_session(session_row):
        renew_session_expiry(session_row)
        await db.commit()

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


async def get_current_session(
    request: Request, db: AsyncSession = Depends(get_db_session)
) -> Session:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session_row = (
        await db.execute(select(Session).where(Session.token_hash == hash_token(raw_token)))
    ).scalar_one_or_none()
    if not session_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    if session_is_absolute_expired(session_row) or session_is_idle_expired(session_row):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return session_row


async def require_recent_step_up(
    current_session: Session = Depends(get_current_session),
) -> Session:
    if not session_step_up_is_fresh(current_session):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Please re-authenticate recently before performing this action.",
        )
    return current_session
