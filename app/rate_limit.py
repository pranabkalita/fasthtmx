from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import Request
from fastapi import HTTPException, status
from redis.asyncio import Redis


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
    if request.client:
        return request.client.host
    return "unknown"


async def apply_rate_limits(redis: Redis, rules_and_ids: list[tuple[LimitRule, str]]) -> None:
    limiter = RateLimiter(redis)
    for rule, identity in rules_and_ids:
        await limiter.hit(rule, identity)
