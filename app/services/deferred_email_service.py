from __future__ import annotations

import json
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DeferredEmailJob
from app.services.time import utcnow_naive


def _safe_dumps(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


def _safe_loads(payload: str, fallback: Any) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return fallback


async def defer_templated_email(
    db: AsyncSession,
    *,
    subject: str,
    recipients: list[str],
    template_name: str,
    context: dict[str, Any],
    metadata: dict[str, Any] | None = None,
    user_id: str | None = None,
) -> DeferredEmailJob:
    row = DeferredEmailJob(
        user_id=user_id,
        subject=subject,
        recipients_json=_safe_dumps(recipients),
        template_name=template_name,
        context_json=_safe_dumps(context),
        metadata_json=_safe_dumps(metadata or {}),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def fetch_due_deferred_email_jobs(db: AsyncSession, *, limit: int = 25) -> list[DeferredEmailJob]:
    now = utcnow_naive()
    rows = (
        await db.execute(
            select(DeferredEmailJob)
            .where(
                and_(
                    DeferredEmailJob.status.in_(["pending", "retrying"]),
                    DeferredEmailJob.available_at <= now,
                )
            )
            .order_by(DeferredEmailJob.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


def parse_recipients(row: DeferredEmailJob) -> list[str]:
    recipients = _safe_loads(row.recipients_json, [])
    if not isinstance(recipients, list):
        return []
    return [str(value) for value in recipients if value]


def parse_context(row: DeferredEmailJob) -> dict[str, Any]:
    context = _safe_loads(row.context_json, {})
    if not isinstance(context, dict):
        return {}
    return context


async def get_deferred_email_overview(db: AsyncSession) -> dict[str, int]:
    rows = (
        await db.execute(
            select(DeferredEmailJob.status, func.count(DeferredEmailJob.id)).group_by(DeferredEmailJob.status)
        )
    ).all()
    counts = {status: int(count) for status, count in rows}
    return {
        "pending": counts.get("pending", 0),
        "retrying": counts.get("retrying", 0),
        "failed": counts.get("failed", 0),
        "sent": counts.get("sent", 0),
        "total": sum(counts.values()),
    }


async def get_recent_deferred_email_jobs(db: AsyncSession, *, limit: int = 12) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    rows = (
        await db.execute(select(DeferredEmailJob).order_by(DeferredEmailJob.created_at.desc()).limit(limit))
    ).scalars().all()

    result: list[dict[str, Any]] = []
    for row in rows:
        recipients = parse_recipients(row)
        result.append(
            {
                "id": row.id,
                "template_name": row.template_name,
                "status": row.status,
                "attempts": row.attempts,
                "max_attempts": row.max_attempts,
                "recipient_count": len(recipients),
                "available_at": row.available_at.isoformat(timespec="seconds") if row.available_at else "",
                "last_error": (row.last_error or "")[:240],
                "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
            }
        )

    return result


async def requeue_failed_deferred_email_jobs(db: AsyncSession, *, limit: int = 100) -> int:
    rows = (
        await db.execute(
            select(DeferredEmailJob)
            .where(DeferredEmailJob.status == "failed")
            .order_by(DeferredEmailJob.created_at.asc())
            .limit(limit)
        )
    ).scalars().all()

    if not rows:
        return 0

    now = utcnow_naive()
    for row in rows:
        row.status = "pending"
        row.attempts = 0
        row.available_at = now
        row.last_error = ""

    await db.commit()
    return len(rows)
