from __future__ import annotations

import asyncio
from uuid import uuid4

from sqlalchemy import delete, select

from app.config import get_settings
from app.db.database import AsyncSessionLocal, engine
from app.db.models import Session, User
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


async def _create_verified_user(*, email: str, password: str = "Password123!") -> User:
    async with AsyncSessionLocal() as db:
        user = await create_user(db=db, email=email, password=password, full_name="Layout User")
        user.is_verified = True
        await db.commit()
        await db.refresh(user)
        return user


async def _create_session_cookie(*, user_id: str) -> str:
    async with AsyncSessionLocal() as db:
        return await create_session(db=db, user_id=user_id, ip_address="127.0.0.1", user_agent="pytest")


async def _delete_user_related_rows(*, user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(delete(Session).where(Session.user_id == user_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def test_guest_layout_has_top_nav_on_landing(test_client):
    response = test_client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert 'data-layout="guest"' in response.text
    assert 'id="guest-top-nav"' in response.text


def test_guest_layout_keeps_top_nav_even_for_logged_in_user(test_client):
    email = f"layout-guest-{uuid4().hex[:8]}@example.com"
    user = _run(_create_verified_user(email=email))
    session_token = _run(_create_session_cookie(user_id=user.id))
    test_client.cookies.set(settings.session_cookie_name, session_token)
    _dispose_engine()

    response = test_client.get("/", follow_redirects=False)
    try:
        assert response.status_code == 200
        assert 'data-layout="guest"' in response.text
        assert 'id="guest-top-nav"' in response.text
        assert 'href="/dashboard" data-layout-nav="cross" hx-boost="false"' in response.text
    finally:
        _run(_delete_user_related_rows(user_id=user.id))


def test_auth_layout_hides_guest_nav_on_dashboard(test_client):
    email = f"layout-auth-{uuid4().hex[:8]}@example.com"
    user = _run(_create_verified_user(email=email))
    session_token = _run(_create_session_cookie(user_id=user.id))
    test_client.cookies.set(settings.session_cookie_name, session_token)
    _dispose_engine()

    response = test_client.get("/dashboard", follow_redirects=False)
    try:
        assert response.status_code == 200
        assert 'data-layout="auth"' in response.text
        assert 'id="guest-top-nav"' not in response.text
    finally:
        _run(_delete_user_related_rows(user_id=user.id))
