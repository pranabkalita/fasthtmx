from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.models import Session
from app.services.auth_service import (
    renew_session_expiry,
    session_is_absolute_expired,
    session_is_idle_expired,
    session_step_up_is_fresh,
)
from app.services.time import as_utc_naive


def _session(
    *,
    expires_in_seconds: int,
    absolute_in_seconds: int,
    step_up_in_seconds: int,
    remember_me: bool = False,
) -> Session:
    now = datetime.now(UTC)
    return Session(
        user_id="user-1",
        token_hash="x" * 64,
        ip_address="127.0.0.1",
        user_agent="pytest",
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(seconds=expires_in_seconds),
        absolute_expires_at=now + timedelta(seconds=absolute_in_seconds),
        step_up_verified_at=now + timedelta(seconds=step_up_in_seconds),
        remember_me=remember_me,
    )


def test_idle_expiry_detected() -> None:
    row = _session(expires_in_seconds=-1, absolute_in_seconds=600, step_up_in_seconds=0)
    assert session_is_idle_expired(row) is True
    assert session_is_absolute_expired(row) is False


def test_absolute_expiry_detected() -> None:
    row = _session(expires_in_seconds=10, absolute_in_seconds=-1, step_up_in_seconds=0)
    assert session_is_idle_expired(row) is False
    assert session_is_absolute_expired(row) is True


def test_renewal_caps_at_absolute_expiry() -> None:
    row = _session(expires_in_seconds=10, absolute_in_seconds=20, step_up_in_seconds=0)
    renew_session_expiry(row)
    assert as_utc_naive(row.expires_at) <= as_utc_naive(row.absolute_expires_at)


def test_step_up_fresh_for_recent_auth() -> None:
    row = _session(expires_in_seconds=120, absolute_in_seconds=1200, step_up_in_seconds=-30)
    assert session_step_up_is_fresh(row) is True
