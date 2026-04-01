from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import pyotp
from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import (
    BackupRecoveryCode,
    EmailVerificationToken,
    LoginAttempt,
    PasswordResetToken,
    Session,
    User,
)
from app.security import (
    TOKEN_PURPOSE_RESET,
    TOKEN_PURPOSE_VERIFY,
    decrypt_secret,
    encrypt_secret,
    generate_raw_token,
    hash_password,
    hash_token,
    issue_signed_token,
    load_signed_token,
    verify_password,
)
from app.services.time import as_utc_naive, utcnow_naive

settings = get_settings()


async def create_user(db: AsyncSession, email: str, password: str, full_name: str = "") -> User:
    existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")

    user = User(email=email, password_hash=hash_password(password), full_name=full_name)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def create_email_verification_token(db: AsyncSession, user_id: str) -> tuple[str, datetime]:
    raw_token = generate_raw_token()
    token_hash = hash_token(raw_token)
    expires_at = utcnow_naive() + timedelta(hours=24)
    db.add(EmailVerificationToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at))
    await db.commit()
    signed = issue_signed_token(raw_token, TOKEN_PURPOSE_VERIFY)
    return signed, expires_at


async def verify_email_token(db: AsyncSession, signed_token: str) -> User:
    raw_token = load_signed_token(signed_token, TOKEN_PURPOSE_VERIFY, 60 * 60 * 24)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired token")

    token_hash = hash_token(raw_token)
    query = select(EmailVerificationToken).where(
        and_(
            EmailVerificationToken.token_hash == token_hash,
            EmailVerificationToken.consumed_at.is_(None),
        )
    )
    token = (await db.execute(query)).scalar_one_or_none()
    if not token or as_utc_naive(token.expires_at) < utcnow_naive():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token is no longer valid")

    user = (await db.execute(select(User).where(User.id == token.user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    token.consumed_at = utcnow_naive()
    user.is_verified = True
    await db.commit()
    await db.refresh(user)
    return user


async def is_locked_out(db: AsyncSession, email: str) -> bool:
    window_start = utcnow_naive() - timedelta(minutes=settings.login_lockout_minutes)
    global_threshold = max(settings.login_max_attempts_account_window, settings.login_max_attempts)
    count_stmt = (
        select(func.count(LoginAttempt.id))
        .where(
            and_(
                LoginAttempt.email == email,
                LoginAttempt.success.is_(False),
                LoginAttempt.attempted_at >= window_start,
            )
        )
        .limit(global_threshold)
    )
    count = (await db.execute(count_stmt)).scalar_one()
    return count >= global_threshold


async def is_locked_out_for_ip(db: AsyncSession, email: str, ip: str | None) -> bool:
    if not ip:
        return False
    window_start = utcnow_naive() - timedelta(minutes=settings.login_lockout_minutes)
    count_stmt = (
        select(func.count(LoginAttempt.id))
        .where(
            and_(
                LoginAttempt.email == email,
                LoginAttempt.ip_address == ip,
                LoginAttempt.success.is_(False),
                LoginAttempt.attempted_at >= window_start,
            )
        )
        .limit(settings.login_max_attempts_per_ip)
    )
    count = (await db.execute(count_stmt)).scalar_one()
    return count >= settings.login_max_attempts_per_ip


async def record_login_attempt(db: AsyncSession, email: str, ip: str | None, success: bool) -> None:
    db.add(LoginAttempt(email=email, ip_address=ip, success=success))
    await db.commit()


async def authenticate_user(db: AsyncSession, email: str, password: str) -> User | None:
    user = (await db.execute(select(User).where(User.email == email, User.is_active.is_(True)))).scalar_one_or_none()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


async def create_session(
    db: AsyncSession,
    user_id: str,
    ip_address: str | None,
    user_agent: str | None,
) -> str:
    raw_session_token = generate_raw_token()
    token_hash = hash_token(raw_session_token)
    expires_at = utcnow_naive() + timedelta(seconds=settings.session_max_age)
    db.add(
        Session(
            user_id=user_id,
            token_hash=token_hash,
            ip_address=ip_address,
            user_agent=user_agent,
            expires_at=expires_at,
        )
    )
    await db.commit()
    return raw_session_token


async def revoke_session(db: AsyncSession, raw_session_token: str) -> None:
    await db.execute(delete(Session).where(Session.token_hash == hash_token(raw_session_token)))
    await db.commit()


async def revoke_session_by_id(db: AsyncSession, user_id: str, session_id: str) -> bool:
    result = await db.execute(
        delete(Session).where(
            and_(
                Session.id == session_id,
                Session.user_id == user_id,
            )
        )
    )
    await db.commit()
    return bool(result.rowcount)


async def revoke_all_sessions(db: AsyncSession, user_id: str) -> None:
    await db.execute(delete(Session).where(Session.user_id == user_id))
    await db.commit()


async def create_reset_token(db: AsyncSession, user_id: str) -> tuple[str, datetime]:
    raw_token = generate_raw_token()
    token_hash = hash_token(raw_token)
    expires_at = utcnow_naive() + timedelta(minutes=settings.reset_token_expiry_minutes)
    db.add(PasswordResetToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at))
    await db.commit()
    signed = issue_signed_token(raw_token, TOKEN_PURPOSE_RESET)
    return signed, expires_at


async def consume_reset_token(db: AsyncSession, signed_token: str, new_password: str) -> User:
    raw_token = load_signed_token(signed_token, TOKEN_PURPOSE_RESET, 60 * settings.reset_token_expiry_minutes)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired reset token")

    token_hash = hash_token(raw_token)
    token = (
        await db.execute(
            select(PasswordResetToken).where(
                and_(
                    PasswordResetToken.token_hash == token_hash,
                    PasswordResetToken.consumed_at.is_(None),
                )
            )
        )
    ).scalar_one_or_none()
    if not token or as_utc_naive(token.expires_at) < utcnow_naive():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Reset token is no longer valid")

    user = (await db.execute(select(User).where(User.id == token.user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    token.consumed_at = utcnow_naive()
    user.password_hash = hash_password(new_password)
    await db.commit()
    await db.refresh(user)
    return user


def build_totp_uri(user: User) -> tuple[str, str]:
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=user.email, issuer_name=settings.app_name)
    return secret, uri


def verify_totp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


def get_user_totp_secret(user: User) -> str | None:
    encrypted = user.two_factor_secret_encrypted
    if encrypted:
        return decrypt_secret(encrypted)
    return user.two_factor_secret


def set_user_totp_secret(user: User, secret: str | None) -> None:
    if secret:
        user.two_factor_secret_encrypted = encrypt_secret(secret)
    else:
        user.two_factor_secret_encrypted = None
    # Keep legacy column empty going forward.
    user.two_factor_secret = None


def generate_backup_code_values(count: int = 8) -> list[str]:
    codes: set[str] = set()
    while len(codes) < count:
        part_a = secrets.token_hex(2).upper()
        part_b = secrets.token_hex(2).upper()
        codes.add(f"{part_a}-{part_b}")
    return sorted(codes)


async def reset_backup_codes(db: AsyncSession, user_id: str, count: int = 8) -> list[str]:
    await db.execute(delete(BackupRecoveryCode).where(BackupRecoveryCode.user_id == user_id))
    codes = generate_backup_code_values(count=count)
    for code in codes:
        db.add(BackupRecoveryCode(user_id=user_id, code_hash=hash_token(code)))
    await db.commit()
    return codes


async def consume_backup_code(db: AsyncSession, user_id: str, code: str) -> bool:
    clean_code = code.strip().upper()
    if not clean_code:
        return False

    hashed_code = hash_token(clean_code)
    row = (
        await db.execute(
            select(BackupRecoveryCode).where(
                and_(
                    BackupRecoveryCode.user_id == user_id,
                    BackupRecoveryCode.code_hash == hashed_code,
                    BackupRecoveryCode.used_at.is_(None),
                )
            )
        )
    ).scalar_one_or_none()
    if not row:
        return False

    row.used_at = utcnow_naive()
    await db.commit()
    return True
