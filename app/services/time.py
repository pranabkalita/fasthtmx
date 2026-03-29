from __future__ import annotations

from datetime import UTC, datetime


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)
