from __future__ import annotations

from email_validator import EmailNotValidError, validate_email
from pydantic import ValidationError


def normalize_email(value: str) -> str:
    clean_email = value.strip().lower()
    try:
        return validate_email(clean_email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError("Please enter a valid email address.") from exc


def first_validation_error(exc: ValidationError, default: str = "Invalid input.") -> str:
    errors = exc.errors()
    if not errors:
        return default

    message = errors[0].get("msg")
    if not message:
        return default

    if message.startswith("Value error, "):
        return message.replace("Value error, ", "", 1)

    return str(message)
