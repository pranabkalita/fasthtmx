from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import delete, select

from app.db.database import AsyncSessionLocal, engine
from app.db.models import DeferredEmailJob, EmailVerificationToken, PasswordResetToken, Session, User
from app.services.auth_service import create_session, create_user
from app.services.job_queue import JobEnqueueError


def _dispose_engine() -> None:
    asyncio.run(engine.dispose())


def _run(coro):
    _dispose_engine()
    try:
        return asyncio.run(coro)
    finally:
        _dispose_engine()


async def _delete_user_related_rows(*, email: str | None = None, user_id: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        resolved_user_id = user_id
        if resolved_user_id is None and email is not None:
            user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
            resolved_user_id = user.id if user else None

        if resolved_user_id:
            await db.execute(delete(EmailVerificationToken).where(EmailVerificationToken.user_id == resolved_user_id))
            await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == resolved_user_id))
            await db.execute(delete(Session).where(Session.user_id == resolved_user_id))
            await db.execute(delete(DeferredEmailJob).where(DeferredEmailJob.user_id == resolved_user_id))
            await db.execute(delete(User).where(User.id == resolved_user_id))
            await db.commit()


async def _count_deferred_jobs(*, user_id: str) -> int:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(DeferredEmailJob).where(DeferredEmailJob.user_id == user_id))).scalars().all()
        return len(rows)


async def _get_user_by_email(*, email: str) -> User | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def _create_verified_user(*, email: str, password: str = "Password123", full_name: str = "Test User") -> User:
    async with AsyncSessionLocal() as db:
        user = await create_user(db=db, email=email, password=password, full_name=full_name)
        user.is_verified = True
        await db.commit()
        await db.refresh(user)
        return user


async def _create_session_cookie(*, user_id: str) -> str:
    async with AsyncSessionLocal() as db:
        return await create_session(db=db, user_id=user_id, ip_address="127.0.0.1", user_agent="pytest")


def test_register_queues_verification_email(test_client, mock_email_queue):
    email = f"register-{uuid4().hex[:8]}@example.com"
    _dispose_engine()

    response = test_client.post(
        "/register",
        data={
            "email": email,
            "full_name": "Queue Test",
            "password": "Password123!",
            "confirm_password": "Password123!",
        },
        follow_redirects=False,
    )

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        queue_mock = mock_email_queue["auth_public"]
        queue_mock.assert_awaited_once()
        call = queue_mock.await_args.kwargs
        assert call["subject"] == "Verify your account"
        assert call["recipients"] == [email]
        assert call["template_name"] == "verify_account"
        assert call["context"]["action_url"].startswith("http")
        assert "/verify-email?token=" in call["context"]["action_url"]
    finally:
        _run(_delete_user_related_rows(email=email))


def test_register_defers_email_when_queue_fails(test_client, mock_email_queue):
    email = f"register-fail-{uuid4().hex[:8]}@example.com"
    mock_email_queue["auth_public"].side_effect = JobEnqueueError("queue down")
    _dispose_engine()

    response = test_client.post(
        "/register",
        data={
            "email": email,
            "full_name": "Queue Fail",
            "password": "Password123!",
            "confirm_password": "Password123!",
        },
        follow_redirects=False,
    )

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        user = _run(_get_user_by_email(email=email))
        assert user is not None
        assert _run(_count_deferred_jobs(user_id=user.id)) >= 1
    finally:
        _run(_delete_user_related_rows(email=email))


def test_forgot_password_queues_reset_email(test_client, mock_email_queue):
    email = f"forgot-{uuid4().hex[:8]}@example.com"
    user = _run(_create_verified_user(email=email, full_name="Forgot Queue"))
    _dispose_engine()

    response = test_client.post(
        "/forgot-password",
        data={"email": email},
        follow_redirects=False,
    )

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        queue_mock = mock_email_queue["auth_recovery"]
        queue_mock.assert_awaited_once()
        call = queue_mock.await_args.kwargs
        assert call["subject"] == "Reset your password"
        assert call["recipients"] == [email]
        assert call["template_name"] == "reset_password"
        assert "/reset-password?token=" in call["context"]["action_url"]
    finally:
        _run(_delete_user_related_rows(user_id=user.id))


def test_resend_verification_defers_when_queue_fails(test_client, mock_email_queue):
    email = f"resend-fail-{uuid4().hex[:8]}@example.com"
    user = _run(_create_verified_user(email=email, full_name="Resend Queue"))
    mock_email_queue["auth_public"].side_effect = JobEnqueueError("queue down")
    _dispose_engine()

    async def _mark_unverified() -> None:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(User).where(User.id == user.id))).scalar_one()
            row.is_verified = False
            await db.commit()

    _run(_mark_unverified())
    _dispose_engine()

    response = test_client.post(
        "/verify-email/resend",
        data={"email": email},
        follow_redirects=False,
    )

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert _run(_count_deferred_jobs(user_id=user.id)) >= 1
    finally:
        _run(_delete_user_related_rows(user_id=user.id))


def test_profile_email_change_queues_verification_email(test_client, mock_email_queue):
    original_email = f"profile-{uuid4().hex[:8]}@example.com"
    new_email = f"profile-new-{uuid4().hex[:8]}@example.com"
    user = _run(_create_verified_user(email=original_email, full_name="Profile Queue"))
    session_token = _run(_create_session_cookie(user_id=user.id))
    test_client.cookies.set("session_id", session_token)
    _dispose_engine()

    response = test_client.post(
        "/profile/update",
        data={"full_name": "Profile Queue Updated", "email": new_email},
        follow_redirects=False,
    )

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/profile"
        queue_mock = mock_email_queue["dashboard"]
        queue_mock.assert_awaited_once()
        call = queue_mock.await_args.kwargs
        assert call["subject"] == "Verify your new email address"
        assert call["recipients"] == [new_email]
        assert call["template_name"] == "verify_new_email"
        assert "/verify-email?token=" in call["context"]["action_url"]
    finally:
        _run(_delete_user_related_rows(user_id=user.id))


def test_profile_email_change_defers_when_queue_fails(test_client, mock_email_queue):
    original_email = f"profile-fail-{uuid4().hex[:8]}@example.com"
    new_email = f"profile-fail-new-{uuid4().hex[:8]}@example.com"
    user = _run(_create_verified_user(email=original_email, full_name="Profile Queue Fail"))
    session_token = _run(_create_session_cookie(user_id=user.id))
    test_client.cookies.set("session_id", session_token)
    mock_email_queue["dashboard"].side_effect = JobEnqueueError("queue down")
    _dispose_engine()

    response = test_client.post(
        "/profile/update",
        data={"full_name": "Profile Queue Updated", "email": new_email},
        follow_redirects=False,
    )

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/profile"
        assert _run(_count_deferred_jobs(user_id=user.id)) >= 1
    finally:
        _run(_delete_user_related_rows(user_id=user.id))
