from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.database import AsyncSessionLocal, engine
from app.db.models import DeferredEmailJob, Session, User
from app.services.auth_service import create_session, create_user

settings = get_settings()


def _dispose_engine() -> None:
    asyncio.run(engine.dispose())


def _run(coro):
    _dispose_engine()
    try:
        return asyncio.run(coro)
    finally:
        _dispose_engine()


async def _create_user_with_session(*, email: str, is_admin: bool) -> tuple[User, str]:
    async with AsyncSessionLocal() as db:
        user = await create_user(db=db, email=email, password="Password123", full_name="Admin Test")
        user.is_verified = True
        user.is_admin = is_admin
        await db.commit()
        await db.refresh(user)
        session_token = await create_session(db=db, user_id=user.id, ip_address="127.0.0.1", user_agent="pytest")
        return user, session_token


async def _insert_deferred_job(*, user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        row = DeferredEmailJob(
            user_id=user_id,
            subject="Verify your account",
            recipients_json='["ops@example.com"]',
            template_name="verify_account",
            context_json='{"action_url":"https://example.test"}',
            metadata_json='{"route":"register"}',
            status="pending",
            attempts=0,
            max_attempts=5,
        )
        db.add(row)
        await db.commit()


async def _insert_failed_deferred_job(*, user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        row = DeferredEmailJob(
            user_id=user_id,
            subject="Reset your password",
            recipients_json='["ops@example.com"]',
            template_name="reset_password",
            context_json='{"action_url":"https://example.test"}',
            metadata_json='{"route":"forgot_password"}',
            status="failed",
            attempts=5,
            max_attempts=5,
            last_error="smtp timeout",
        )
        db.add(row)
        await db.commit()


async def _get_deferred_job(*, user_id: str) -> DeferredEmailJob | None:
    async with AsyncSessionLocal() as db:
        return (
            await db.execute(
                select(DeferredEmailJob).where(DeferredEmailJob.user_id == user_id).order_by(DeferredEmailJob.created_at.desc())
            )
        ).scalars().first()


async def _cleanup_user(*, user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(DeferredEmailJob).where(DeferredEmailJob.user_id == user_id))
        await db.execute(delete(Session).where(Session.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


async def _find_user(*, email: str) -> User:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one()


def test_admin_can_view_queue_status_with_deferred_section(test_client):
    email = f"admin-queue-{uuid4().hex[:8]}@example.com"
    user, session_token = _run(_create_user_with_session(email=email, is_admin=True))
    _run(_insert_deferred_job(user_id=user.id))
    test_client.cookies.set(settings.session_cookie_name, session_token)
    _dispose_engine()

    response = test_client.get("/admin/queue-status", follow_redirects=False)

    try:
        assert response.status_code == 200
        assert "Deferred Email Jobs" in response.text
        assert "Recent Deferred Jobs" in response.text
    finally:
        _run(_cleanup_user(user_id=user.id))


def test_non_admin_cannot_view_queue_status(test_client):
    email = f"user-queue-{uuid4().hex[:8]}@example.com"
    user, session_token = _run(_create_user_with_session(email=email, is_admin=False))
    test_client.cookies.set(settings.session_cookie_name, session_token)
    _dispose_engine()

    response = test_client.get("/admin/queue-status", follow_redirects=False)

    try:
        assert response.status_code == 403
    finally:
        _run(_cleanup_user(user_id=user.id))


def test_admin_can_requeue_failed_deferred_jobs(test_client):
    email = f"admin-requeue-{uuid4().hex[:8]}@example.com"
    user, session_token = _run(_create_user_with_session(email=email, is_admin=True))
    _run(_insert_failed_deferred_job(user_id=user.id))
    test_client.cookies.set(settings.session_cookie_name, session_token)
    _dispose_engine()

    response = test_client.post("/admin/queue-status/deferred/requeue-failed", follow_redirects=False)

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/admin/queue-status"
        row = _run(_get_deferred_job(user_id=user.id))
        assert row is not None
        assert row.status == "pending"
        assert row.attempts == 0
    finally:
        _run(_cleanup_user(user_id=user.id))


def test_non_admin_cannot_requeue_failed_deferred_jobs(test_client):
    email = f"user-requeue-{uuid4().hex[:8]}@example.com"
    user, session_token = _run(_create_user_with_session(email=email, is_admin=False))
    _run(_insert_failed_deferred_job(user_id=user.id))
    test_client.cookies.set(settings.session_cookie_name, session_token)
    _dispose_engine()

    response = test_client.post("/admin/queue-status/deferred/requeue-failed", follow_redirects=False)

    try:
        assert response.status_code == 403
        row = _run(_get_deferred_job(user_id=user.id))
        assert row is not None
        assert row.status == "failed"
        assert row.attempts == 5
    finally:
        _run(_cleanup_user(user_id=user.id))
