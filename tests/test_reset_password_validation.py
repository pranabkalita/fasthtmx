from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import delete, select

from app.db.database import AsyncSessionLocal, engine
from app.db.models import PasswordResetToken, Session, User
from app.security import verify_password
from app.services.auth_service import create_reset_token, create_user


def _dispose_engine() -> None:
    asyncio.run(engine.dispose())


def _run(coro):
    _dispose_engine()
    try:
        return asyncio.run(coro)
    finally:
        _dispose_engine()


async def _create_user_and_reset_token(*, email: str, password: str = "Password123") -> tuple[str, str]:
    async with AsyncSessionLocal() as db:
        user = await create_user(db=db, email=email, password=password, full_name="Reset Validation")
        signed_token, _ = await create_reset_token(db=db, user_id=user.id)
        return user.id, signed_token


async def _get_user(*, user_id: str) -> User:
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
        return user


async def _delete_user_related_rows(*, user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
        await db.execute(delete(Session).where(Session.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def test_reset_password_rejects_mismatch(test_client):
    email = f"reset-mismatch-{uuid4().hex[:8]}@example.com"
    user_id, token = _run(_create_user_and_reset_token(email=email))
    _dispose_engine()

    response = test_client.post(
        "/reset-password",
        data={
            "token": token,
            "new_password": "StrongPass1!",
            "confirm_new_password": "StrongPass2!",
        },
        follow_redirects=False,
    )

    try:
        assert response.status_code == 400
        assert "retype password do not match" in response.text
    finally:
        _run(_delete_user_related_rows(user_id=user_id))


def test_reset_password_rejects_weak_password(test_client):
    email = f"reset-weak-{uuid4().hex[:8]}@example.com"
    user_id, token = _run(_create_user_and_reset_token(email=email))
    _dispose_engine()

    response = test_client.post(
        "/reset-password",
        data={
            "token": token,
            "new_password": "weakpass",
            "confirm_new_password": "weakpass",
        },
        follow_redirects=False,
    )

    try:
        assert response.status_code == 400
        assert "at least one uppercase letter" in response.text
    finally:
        _run(_delete_user_related_rows(user_id=user_id))


def test_reset_password_accepts_strong_matching_password(test_client):
    email = f"reset-strong-{uuid4().hex[:8]}@example.com"
    user_id, token = _run(_create_user_and_reset_token(email=email, password="Password123"))
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
        user = _run(_get_user(user_id=user_id))
        assert verify_password("NewStrong1!", user.password_hash)
    finally:
        _run(_delete_user_related_rows(user_id=user_id))