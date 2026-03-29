from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

from sqlalchemy import delete

from app.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import DeferredEmailJob, User
from app.services.deferred_email_service import fetch_due_deferred_email_jobs, parse_context, parse_recipients
from app.services.email_service import send_templated_email
from app.services.time import utcnow_naive

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


async def retry_deferred_email_jobs(ctx: dict | None = None) -> int:
    _ = ctx
    processed = 0

    async with AsyncSessionLocal() as session:
        rows = await fetch_due_deferred_email_jobs(session, limit=25)

        for row in rows:
            processed += 1
            row.attempts += 1
            row.status = "retrying"

            recipients = parse_recipients(row)
            context = parse_context(row)

            try:
                await send_templated_email(
                    subject=row.subject,
                    recipients=recipients,
                    template_name=row.template_name,
                    context=context,
                )
            except Exception as exc:
                row.last_error = str(exc)[:800]
                if row.attempts >= row.max_attempts:
                    row.status = "failed"
                else:
                    row.status = "retrying"
                    backoff_minutes = min(60, 2 ** row.attempts)
                    row.available_at = utcnow_naive() + timedelta(minutes=backoff_minutes)
                logger.exception(
                    "deferred_email_retry_failed",
                    extra={
                        "deferred_email_job_id": row.id,
                        "template_name": row.template_name,
                        "attempts": row.attempts,
                    },
                )
                continue

            row.status = "sent"
            row.sent_at = utcnow_naive()
            row.last_error = ""
            logger.info(
                "deferred_email_retry_succeeded",
                extra={
                    "deferred_email_job_id": row.id,
                    "template_name": row.template_name,
                    "attempts": row.attempts,
                },
            )

        if processed:
            await session.commit()

    return processed
