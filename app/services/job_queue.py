from __future__ import annotations

import asyncio
import logging
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_job_queue: ArqRedis | None = None
_job_queue_lock = asyncio.Lock()


class JobEnqueueError(RuntimeError):
    pass


async def get_job_queue() -> ArqRedis:
    global _job_queue

    if _job_queue is not None:
        return _job_queue

    async with _job_queue_lock:
        if _job_queue is None:
            redis_settings = RedisSettings.from_dsn(settings.redis_url)
            _job_queue = await create_pool(redis_settings)
            logger.info(
                "job_queue_connected",
                extra={
                    "redis_host": redis_settings.host,
                    "redis_port": redis_settings.port,
                    "redis_db": redis_settings.database,
                },
            )

    return _job_queue


async def close_job_queue() -> None:
    global _job_queue

    if _job_queue is None:
        return

    await _job_queue.aclose()
    logger.info("job_queue_closed")
    _job_queue = None


async def is_job_queue_healthy() -> bool:
    try:
        queue = await get_job_queue()
        await queue.ping()
    except Exception:
        logger.exception("job_queue_unhealthy")
        return False
    return True


async def enqueue_templated_email(
    *,
    subject: str,
    recipients: list[str],
    template_name: str,
    context: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> str:
    extra_meta = metadata or {}
    try:
        queue = await get_job_queue()
        job = await queue.enqueue_job(
            "send_templated_email_job",
            subject=subject,
            recipients=recipients,
            template_name=template_name,
            context=context,
        )
    except Exception as exc:
        logger.exception(
            "templated_email_enqueue_failed",
            extra={
                "template_name": template_name,
                "recipient_count": len(recipients),
                "subject": subject,
                **extra_meta,
            },
        )
        raise JobEnqueueError("Unable to queue email delivery.") from exc

    if job is None:
        logger.error(
            "templated_email_enqueue_returned_none",
            extra={
                "template_name": template_name,
                "recipient_count": len(recipients),
                "subject": subject,
                **extra_meta,
            },
        )
        raise JobEnqueueError("Unable to queue email delivery.")

    logger.info(
        "templated_email_enqueued",
        extra={
            "job_id": job.job_id,
            "template_name": template_name,
            "recipient_count": len(recipients),
            "subject": subject,
            **extra_meta,
        },
    )

    return job.job_id


async def get_recent_email_job_results(limit: int = 10) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    try:
        queue = await get_job_queue()
        results = await queue.all_job_results()
    except Exception:
        logger.exception("recent_email_job_results_failed")
        return []

    email_results = [r for r in results if r.function == "send_templated_email_job"]
    email_results = list(reversed(email_results[-limit:]))

    summarized: list[dict[str, Any]] = []
    for job in email_results:
        kwargs = job.kwargs or {}
        recipients = kwargs.get("recipients", [])
        template_name = kwargs.get("template_name", "unknown")
        error_text = ""
        if not job.success:
            error_text = str(job.result)
            if len(error_text) > 240:
                error_text = error_text[:237] + "..."

        summarized.append(
            {
                "job_id": job.job_id,
                "template_name": template_name,
                "recipient_count": len(recipients) if isinstance(recipients, list) else 0,
                "success": bool(job.success),
                "enqueue_time": job.enqueue_time.isoformat(timespec="seconds"),
                "start_time": job.start_time.isoformat(timespec="seconds"),
                "finish_time": job.finish_time.isoformat(timespec="seconds"),
                "error": error_text,
            }
        )

    return summarized
