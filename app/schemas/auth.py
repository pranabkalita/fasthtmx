from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.schemas._common import normalize_email


class RegistrationForm(BaseModel):
    email: str
    full_name: str = ""
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        clean_name = value.strip()
        if len(clean_name) > 120:
            raise ValueError("Name must be 120 characters or fewer.")
        return clean_name

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return value


class LoginForm(BaseModel):
    email: str
    password: str
    two_factor_code: str = ""

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("two_factor_code")
    @classmethod
    def normalize_two_factor_code(cls, value: str) -> str:
        return value.strip()


class ResendVerificationForm(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)
