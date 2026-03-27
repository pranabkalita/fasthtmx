import hashlib
import secrets
from datetime import UTC, datetime

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(settings.secret_key)


TOKEN_PURPOSE_VERIFY = "verify_email"
TOKEN_PURPOSE_RESET = "password_reset"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    return pwd_context.verify(password, hashed_password)


def generate_raw_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_signed_token(subject: str, purpose: str) -> str:
    payload = {
        "sub": subject,
        "purpose": purpose,
        "iat": int(datetime.now(UTC).timestamp()),
    }
    return serializer.dumps(payload)


def load_signed_token(token: str, purpose: str, max_age: int) -> str | None:
    try:
        payload = serializer.loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if payload.get("purpose") != purpose:
        return None
    return payload.get("sub")
