from __future__ import annotations

from arq.cron import cron
from arq.connections import RedisSettings
from arq.worker import func

from app.config import get_settings
from app.jobs import purge_deactivated_users, retry_deferred_email_jobs, send_templated_email_job

settings = get_settings()


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    retry_jobs = True
    functions = [
        func(purge_deactivated_users, timeout=300, keep_result=0, max_tries=1),
        func(send_templated_email_job, timeout=120, keep_result=3600, max_tries=3),
        func(retry_deferred_email_jobs, timeout=180, keep_result=3600, max_tries=1),
    ]
    cron_jobs = [
        cron(
            purge_deactivated_users,
            hour=2,
            minute=0,
        ),
        cron(
            retry_deferred_email_jobs,
            minute={0, 10, 20, 30, 40, 50},
        ),
    ]
