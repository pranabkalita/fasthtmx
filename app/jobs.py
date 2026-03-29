from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from sqlalchemy import delete

from app.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import User
from app.services.email_service import send_templated_email

settings = get_settings()
logger = logging.getLogger(__name__)


async def send_templated_email_job(
    ctx: dict[str, Any],
    *,
    subject: str,
    recipients: list[str],
    template_name: str,
    context: dict[str, Any],
) -> None:
    _ = ctx
    logger.info(
        "templated_email_job_started",
        extra={
            "template_name": template_name,
            "recipient_count": len(recipients),
            "subject": subject,
        },
    )
    try:
        await send_templated_email(
            subject=subject,
            recipients=recipients,
            template_name=template_name,
            context=context,
        )
    except Exception:
        logger.exception(
            "templated_email_job_failed",
            extra={
                "template_name": template_name,
                "recipient_count": len(recipients),
                "subject": subject,
            },
        )
        raise

    logger.info(
        "templated_email_job_completed",
        extra={
            "template_name": template_name,
            "recipient_count": len(recipients),
            "subject": subject,
        },
    )


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
