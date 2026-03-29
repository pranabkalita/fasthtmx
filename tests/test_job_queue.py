from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.services.job_queue import (
    JobEnqueueError,
    enqueue_templated_email,
    get_recent_email_job_results,
    is_job_queue_healthy,
)


class _Job:
    def __init__(self, job_id: str):
        self.job_id = job_id


class _Queue:
    def __init__(self, job: _Job | None):
        self.job = job
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def enqueue_job(self, function: str, **kwargs):
        self.calls.append((function, kwargs))
        return self.job

    async def ping(self):
        return True

    async def all_job_results(self):
        return []


class _Result:
    def __init__(
        self,
        *,
        function: str,
        job_id: str,
        kwargs: dict[str, object],
        success: bool,
        result: object,
    ):
        self.function = function
        self.job_id = job_id
        self.kwargs = kwargs
        self.success = success
        self.result = result
        now = datetime.now(UTC)
        self.enqueue_time = now
        self.start_time = now
        self.finish_time = now


def test_enqueue_templated_email_enqueues_generic_worker_job(monkeypatch):
    from app.services import job_queue

    queue = _Queue(_Job("job-123"))

    async def _fake_get_job_queue():
        return queue

    monkeypatch.setattr(job_queue, "get_job_queue", _fake_get_job_queue)

    job_id = asyncio.run(
        enqueue_templated_email(
            subject="Reset your password",
            recipients=["user@example.com"],
            template_name="reset_password",
            context={"action_url": "https://example.com/reset"},
        )
    )

    assert job_id == "job-123"
    assert queue.calls == [
        (
            "send_templated_email_job",
            {
                "subject": "Reset your password",
                "recipients": ["user@example.com"],
                "template_name": "reset_password",
                "context": {"action_url": "https://example.com/reset"},
            },
        )
    ]


def test_enqueue_templated_email_raises_when_job_not_created(monkeypatch):
    from app.services import job_queue

    queue = _Queue(None)

    async def _fake_get_job_queue():
        return queue

    monkeypatch.setattr(job_queue, "get_job_queue", _fake_get_job_queue)

    try:
        asyncio.run(
            enqueue_templated_email(
                subject="Verify your account",
                recipients=["user@example.com"],
                template_name="verify_account",
                context={"action_url": "https://example.com/verify"},
            )
        )
    except JobEnqueueError:
        assert True
    else:
        assert False, "Expected JobEnqueueError when enqueue_job returns None"


def test_is_job_queue_healthy_returns_true_when_ping_succeeds(monkeypatch):
    from app.services import job_queue

    queue = _Queue(_Job("job-123"))

    async def _fake_get_job_queue():
        return queue

    monkeypatch.setattr(job_queue, "get_job_queue", _fake_get_job_queue)

    assert asyncio.run(is_job_queue_healthy()) is True


def test_is_job_queue_healthy_returns_false_when_ping_fails(monkeypatch):
    from app.services import job_queue

    class _BrokenQueue(_Queue):
        async def ping(self):
            raise RuntimeError("redis unavailable")

    queue = _BrokenQueue(_Job("job-123"))

    async def _fake_get_job_queue():
        return queue

    monkeypatch.setattr(job_queue, "get_job_queue", _fake_get_job_queue)

    assert asyncio.run(is_job_queue_healthy()) is False


def test_get_recent_email_job_results_filters_and_orders(monkeypatch):
    from app.services import job_queue

    class _QueueWithResults(_Queue):
        async def all_job_results(self):
            return [
                _Result(
                    function="purge_deactivated_users",
                    job_id="job-ignored",
                    kwargs={},
                    success=True,
                    result=0,
                ),
                _Result(
                    function="send_templated_email_job",
                    job_id="job-1",
                    kwargs={"template_name": "verify_account", "recipients": ["a@example.com"]},
                    success=True,
                    result="ok",
                ),
                _Result(
                    function="send_templated_email_job",
                    job_id="job-2",
                    kwargs={"template_name": "reset_password", "recipients": ["a@example.com", "b@example.com"]},
                    success=False,
                    result="smtp timeout",
                ),
            ]

    queue = _QueueWithResults(_Job("unused"))

    async def _fake_get_job_queue():
        return queue

    monkeypatch.setattr(job_queue, "get_job_queue", _fake_get_job_queue)

    rows = asyncio.run(get_recent_email_job_results(limit=5))

    assert len(rows) == 2
    assert rows[0]["job_id"] == "job-2"
    assert rows[0]["template_name"] == "reset_password"
    assert rows[0]["recipient_count"] == 2
    assert rows[0]["success"] is False
    assert rows[0]["error"] == "smtp timeout"
    assert rows[1]["job_id"] == "job-1"
    assert rows[1]["success"] is True


def test_get_recent_email_job_results_returns_empty_on_queue_error(monkeypatch):
    from app.services import job_queue

    async def _fake_get_job_queue():
        raise RuntimeError("queue down")

    monkeypatch.setattr(job_queue, "get_job_queue", _fake_get_job_queue)

    rows = asyncio.run(get_recent_email_job_results(limit=5))
    assert rows == []
