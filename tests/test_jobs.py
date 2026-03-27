import asyncio

from app.jobs import purge_deactivated_users


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
