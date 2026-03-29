from __future__ import annotations

import re

from pydantic import BaseModel, field_validator


class EnableTwoFactorForm(BaseModel):
    secret: str
    code: str

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        clean_secret = value.strip()
        if not clean_secret:
            raise ValueError("2FA setup secret is missing. Please try setup again.")
        return clean_secret

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        clean_code = value.strip().upper()
        if not clean_code:
            raise ValueError("Invalid code. Enter a current authenticator code.")
        if re.fullmatch(r"\d{6}", clean_code):
            return clean_code
        raise ValueError("Invalid code. Enter a current authenticator code.")


class DisableTwoFactorForm(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        clean_password = value.strip()
        if not clean_password:
            raise ValueError("Password is incorrect. 2FA was not disabled.")
        return value


class DeactivateAccountForm(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        clean_password = value.strip()
        if not clean_password:
            raise ValueError("Password is incorrect. Account was not deactivated.")
        return value
