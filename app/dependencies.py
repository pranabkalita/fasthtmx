from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db_session
from app.db.models import Session, User
from app.security import hash_token

settings = get_settings()


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


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
    if _as_utc_naive(session_row.expires_at) < _utcnow_naive():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

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
