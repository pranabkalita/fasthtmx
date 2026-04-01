from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import Request
from fastapi import HTTPException, status
from redis.asyncio import Redis

from app.config import get_settings

settings = get_settings()


@dataclass(slots=True)
class LimitRule:
    key_prefix: str
    limit: int
    window_seconds: int


class RateLimiter:
    def __init__(self, redis: Redis):
        self._redis = redis

    async def hit(self, rule: LimitRule, identity: str) -> None:
        key = f"{rule.key_prefix}:{identity}"
        now = int(time.time())
        ttl = rule.window_seconds

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)
            result = await pipe.execute()

        current = int(result[0])
        if current > rule.limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )


def safe_identity(value: str | None) -> str:
    if not value:
        return "unknown"
    return value.strip().lower().replace(" ", "_")


def get_ip(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    if not settings.use_forwarded_headers:
        return client_ip
    if client_ip not in settings.trusted_proxy_ip_set:
        return client_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if not forwarded:
        return client_ip
    first_hop = forwarded.split(",")[0].strip()
    if first_hop:
        return first_hop
    return "unknown"


async def apply_rate_limits(redis: Redis, rules_and_ids: list[tuple[LimitRule, str]]) -> None:
    limiter = RateLimiter(redis)
    for rule, identity in rules_and_ids:
        await limiter.hit(rule, identity)
