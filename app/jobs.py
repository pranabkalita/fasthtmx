from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete

from app.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import User

settings = get_settings()


async def purge_deactivated_users(ctx: dict | None = None) -> int:
    retention_days = int(ctx.get("retention_days", settings.account_purge_days)) if ctx else settings.account_purge_days
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(User).where(
                User.is_active.is_(False),
                User.deleted_at.is_not(None),
                User.deleted_at < cutoff,
            )
        )
        await session.commit()
        return int(result.rowcount or 0)
