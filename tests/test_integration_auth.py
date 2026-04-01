from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.database import AsyncSessionLocal, engine
from app.db.models import EmailVerificationToken, PasswordResetToken, Session, User
from app.security import verify_password
from app.services.auth_service import (
    create_email_verification_token,
    create_reset_token,
    create_session,
    create_user,
)

settings = get_settings()


def _dispose_engine() -> None:
    asyncio.run(engine.dispose())


def _run(coro):
    _dispose_engine()
    try:
        return asyncio.run(coro)
    finally:
        _dispose_engine()


async def _get_user_by_email(email: str) -> User | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def _create_verified_user(*, email: str, password: str = "Password123") -> User:
    async with AsyncSessionLocal() as db:
        user = await create_user(db=db, email=email, password=password, full_name="Integration User")
        user.is_verified = True
        await db.commit()
        await db.refresh(user)
        return user


async def _count_sessions(*, user_id: str) -> int:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Session).where(Session.user_id == user_id))).scalars().all()
        return len(rows)


async def _get_user(*, user_id: str) -> User:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(User).where(User.id == user_id))).scalar_one()


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
            await db.execute(delete(User).where(User.id == resolved_user_id))
            await db.commit()


def test_register_verify_login_and_dashboard_flow(test_client):
    email = f"it-flow-{uuid4().hex[:8]}@example.com"
    password = "Password123!"
    _dispose_engine()

    register_response = test_client.post(
        "/register",
        data={
            "email": email,
            "full_name": "Flow User",
            "password": password,
            "confirm_password": password,
        },
        follow_redirects=False,
    )
    assert register_response.status_code == 303
    assert register_response.headers["location"] == "/login"

    user = _run(_get_user_by_email(email))
    assert user is not None
    assert user.is_verified is False

    # create_email_verification_token needs a managed session; use explicit helper below
    async def _issue_verify_token(user_id: str) -> str:
        async with AsyncSessionLocal() as db:
            token, _ = await create_email_verification_token(db=db, user_id=user_id)
            return token

    signed_token = _run(_issue_verify_token(user.id))
    verify_response = test_client.get(f"/verify-email?token={signed_token}", follow_redirects=False)
    assert verify_response.status_code == 200
    assert "Email verified" in verify_response.text

    login_response = test_client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )

    try:
        assert login_response.status_code == 303
        assert login_response.headers["location"] == "/dashboard"
        assert settings.session_cookie_name in login_response.cookies

        dashboard_response = test_client.get("/dashboard", follow_redirects=False)
        assert dashboard_response.status_code == 200
    finally:
        _run(_delete_user_related_rows(user_id=user.id))


def test_unverified_user_login_rejected(test_client):
    email = f"it-unverified-{uuid4().hex[:8]}@example.com"
    password = "Password123"
    user = _run(_create_verified_user(email=email, password=password))

    async def _mark_unverified(user_id: str) -> None:
        async with AsyncSessionLocal() as db:
            row = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
            row.is_verified = False
            await db.commit()

    _run(_mark_unverified(user.id))
    _dispose_engine()

    response = test_client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )

    try:
        assert response.status_code == 403
        assert "Verify your email before signing in." in response.text
    finally:
        _run(_delete_user_related_rows(user_id=user.id))


def test_invalid_login_shows_error_message(test_client):
    response = test_client.post(
        "/login",
        data={"email": f"missing-{uuid4().hex[:8]}@example.com", "password": "WrongPassword123!"},
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert "Invalid credentials." in response.text


def test_invalid_login_shows_error_message_for_htmx(test_client):
    email = f"missing-htmx-{uuid4().hex[:8]}@example.com"
    two_factor_code = "123456"

    _dispose_engine()

    response = test_client.post(
        "/login",
        data={"email": email, "password": "WrongPassword123!", "two_factor_code": two_factor_code},
        headers={"HX-Request": "true"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "Invalid credentials." in response.text
    assert f'value="{email}"' in response.text
    assert f'value="{two_factor_code}"' in response.text


def test_logout_revokes_current_session(test_client):
    email = f"it-logout-{uuid4().hex[:8]}@example.com"
    user = _run(_create_verified_user(email=email))

    async def _issue_session(user_id: str) -> str:
        async with AsyncSessionLocal() as db:
            return await create_session(db=db, user_id=user_id, ip_address="127.0.0.1", user_agent="pytest")

    session_token = _run(_issue_session(user.id))
    test_client.cookies.set(settings.session_cookie_name, session_token)
    _dispose_engine()

    response = test_client.post("/logout", follow_redirects=False)

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert _run(_count_sessions(user_id=user.id)) == 0
    finally:
        _run(_delete_user_related_rows(user_id=user.id))


def test_password_reset_revokes_all_sessions(test_client):
    email = f"it-reset-{uuid4().hex[:8]}@example.com"
    user = _run(_create_verified_user(email=email, password="Password123"))

    async def _issue_sessions_and_token(user_id: str) -> str:
        async with AsyncSessionLocal() as db:
            await create_session(db=db, user_id=user_id, ip_address="127.0.0.1", user_agent="pytest-a")
            await create_session(db=db, user_id=user_id, ip_address="127.0.0.1", user_agent="pytest-b")
            token, _ = await create_reset_token(db=db, user_id=user_id)
            return token

    token = _run(_issue_sessions_and_token(user.id))
    _dispose_engine()

    response = test_client.post(
        "/reset-password",
        data={
            "token": token,
            "new_password": "NewStrong1!",
            "confirm_new_password": "NewStrong1!",
        },
        follow_redirects=False,
    )

    try:
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert _run(_count_sessions(user_id=user.id)) == 0
        updated_user = _run(_get_user(user_id=user.id))
        assert verify_password("NewStrong1!", updated_user.password_hash)
    finally:
        _run(_delete_user_related_rows(user_id=user.id))
