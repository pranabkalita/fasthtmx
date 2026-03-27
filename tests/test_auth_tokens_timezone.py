from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException, status
from sqlalchemy import delete, select

from app.db.database import AsyncSessionLocal, engine
from app.db.models import EmailVerificationToken, PasswordResetToken, User
from app.services.auth_service import (
    consume_reset_token,
    create_email_verification_token,
    create_reset_token,
    create_user,
    verify_email_token,
)


@pytest.mark.asyncio
async def test_verify_email_token_with_expired_naive_datetime_returns_400() -> None:
    email = f"tz-verify-{uuid4().hex[:8]}@example.com"
    await engine.dispose()

    async with AsyncSessionLocal() as db:
        user = await create_user(db=db, email=email, password="Password123", full_name="TZ Verify")
        signed_token, _ = await create_email_verification_token(db=db, user_id=user.id)

        token_row = (
            await db.execute(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.user_id == user.id,
                    EmailVerificationToken.consumed_at.is_(None),
                )
            )
        ).scalar_one()

        # Force a naive expired timestamp to match MySQL DATETIME behavior.
        token_row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await verify_email_token(db=db, signed_token=signed_token)

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "no longer valid" in exc.value.detail.lower()

        await db.execute(delete(EmailVerificationToken).where(EmailVerificationToken.user_id == user.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()


@pytest.mark.asyncio
async def test_consume_reset_token_with_expired_naive_datetime_returns_400() -> None:
    email = f"tz-reset-{uuid4().hex[:8]}@example.com"
    await engine.dispose()

    async with AsyncSessionLocal() as db:
        user = await create_user(db=db, email=email, password="Password123", full_name="TZ Reset")
        signed_token, _ = await create_reset_token(db=db, user_id=user.id)

        token_row = (
            await db.execute(
                select(PasswordResetToken).where(
                    PasswordResetToken.user_id == user.id,
                    PasswordResetToken.consumed_at.is_(None),
                )
            )
        ).scalar_one()

        # Force a naive expired timestamp to match MySQL DATETIME behavior.
        token_row.expires_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5)
        await db.commit()

        with pytest.raises(HTTPException) as exc:
            await consume_reset_token(db=db, signed_token=signed_token, new_password="NewPassword123")

        assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "no longer valid" in exc.value.detail.lower()

        await db.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()
