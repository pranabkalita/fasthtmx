import hashlib
import secrets
import base64
from datetime import UTC, datetime

from cryptography.fernet import Fernet, InvalidToken
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from passlib.context import CryptContext

from app.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
serializer = URLSafeTimedSerializer(settings.secret_key)


def _build_fernet() -> Fernet:
    # Derive a stable 32-byte key from SECRET_KEY.
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


fernet = _build_fernet()


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


def encrypt_secret(value: str) -> str:
    return fernet.encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str) -> str | None:
    try:
        return fernet.decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None


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
