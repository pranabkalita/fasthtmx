import asyncio

from app.jobs import purge_deactivated_users, send_templated_email_job


class _Result:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _SessionStub:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, stmt):
        _ = stmt
        return _Result(self.rowcount)

    async def commit(self):
        return None


class _SessionFactory:
    def __init__(self, rowcount: int = 1):
        self.rowcount = rowcount

    def __call__(self):
        return _SessionStub(rowcount=self.rowcount)


def test_purge_deactivated_users_returns_deleted_count(monkeypatch):
    from app import jobs

    monkeypatch.setattr(jobs, "AsyncSessionLocal", _SessionFactory(rowcount=3))
    deleted = asyncio.run(purge_deactivated_users(ctx={"retention_days": 30}))
    assert deleted == 3


def test_send_templated_email_job_delegates_to_email_service(monkeypatch):
    from app import jobs

    captured: dict[str, object] = {}

    async def _fake_send_templated_email(*, subject, recipients, template_name, context):
        captured["subject"] = subject
        captured["recipients"] = recipients
        captured["template_name"] = template_name
        captured["context"] = context

    monkeypatch.setattr(jobs, "send_templated_email", _fake_send_templated_email)

    asyncio.run(
        send_templated_email_job(
            {},
            subject="Verify your account",
            recipients=["user@example.com"],
            template_name="verify_account",
            context={"action_url": "https://example.com/verify"},
        )
    )

    assert captured == {
        "subject": "Verify your account",
        "recipients": ["user@example.com"],
        "template_name": "verify_account",
        "context": {"action_url": "https://example.com/verify"},
    }
