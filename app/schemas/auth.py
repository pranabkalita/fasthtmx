from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator

from app.schemas._common import normalize_email
from app.services.password_policy import validate_password_confirmation, validate_strong_password


class RegistrationForm(BaseModel):
    email: str
    full_name: str = ""
    password: str
    confirm_password: str

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
        error = validate_strong_password(value, label="Password")
        if error:
            raise ValueError(error)
        return value

    @model_validator(mode="after")
    def validate_password_match(self) -> "RegistrationForm":
        error = validate_password_confirmation(
            self.password,
            self.confirm_password,
            label="Password",
        )
        if error:
            raise ValueError(error)
        return self


class LoginForm(BaseModel):
    email: str
    password: str
    two_factor_code: str = ""
    remember_me: bool = False

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
