import pytest
from fastapi import HTTPException
from fastapi import Request

from app.rate_limit import LimitRule, RateLimiter, get_ip, safe_identity


class _Pipeline:
    def __init__(self, store: dict[str, int]):
        self.store = store
        self.key = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def incr(self, key: str):
        self.key = key

    def expire(self, key: str, ttl: int, nx: bool = True):
        _ = (key, ttl, nx)

    async def execute(self):
        current = self.store.get(self.key, 0) + 1
        self.store[self.key] = current
        return [current, True]


class _RedisStub:
    def __init__(self):
        self.store: dict[str, int] = {}

    def pipeline(self, transaction: bool = True):
        _ = transaction
        return _Pipeline(self.store)


@pytest.mark.asyncio
async def test_rate_limiter_blocks_after_limit() -> None:
    redis = _RedisStub()
    limiter = RateLimiter(redis)
    rule = LimitRule(key_prefix="rl:test", limit=2, window_seconds=60)

    await limiter.hit(rule, "ip-1")
    await limiter.hit(rule, "ip-1")

    with pytest.raises(HTTPException) as exc:
        await limiter.hit(rule, "ip-1")

    assert exc.value.status_code == 429


def test_safe_identity_normalizes() -> None:
    assert safe_identity("  Alice@example.com ") == "alice@example.com"
    assert safe_identity(None) == "unknown"


def test_get_ip_without_forwarded_headers() -> None:
    scope = {
        "type": "http",
        "headers": [],
        "client": ("203.0.113.1", 1234),
        "method": "GET",
        "path": "/",
    }
    request = Request(scope)
    assert get_ip(request) == "203.0.113.1"
