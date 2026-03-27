from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Session, User
from app.rate_limit import LimitRule, RateLimiter
from app.security import hash_token

settings = get_settings()


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
