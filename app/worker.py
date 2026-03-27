from __future__ import annotations

from arq.cron import cron

from app.config import get_settings
from app.jobs import purge_deactivated_users

settings = get_settings()


class WorkerSettings:
    functions = [purge_deactivated_users]
    cron_jobs = [
        cron(
            purge_deactivated_users,
            hour=2,
            minute=0,
            kwargs={"ctx": {"retention_days": settings.account_purge_days}},
        )
    ]
