from __future__ import annotations

import re


def validate_strong_password(password: str, *, label: str = "New password") -> str | None:
    if len(password) < 8:
        return f"{label} must be at least 8 characters."
    if not re.search(r"[a-z]", password):
        return f"{label} must include at least one lowercase letter."
    if not re.search(r"[A-Z]", password):
        return f"{label} must include at least one uppercase letter."
    if not re.search(r"\d", password):
        return f"{label} must include at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
        return f"{label} must include at least one special character."
    return None


def validate_password_confirmation(password: str, confirm_password: str, *, label: str = "Password") -> str | None:
    if password != confirm_password:
        return f"{label} and retype password do not match."
    return None