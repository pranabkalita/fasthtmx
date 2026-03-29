from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator

from app.schemas._common import normalize_email
from app.services.password_policy import validate_password_confirmation, validate_strong_password


class ForgotPasswordForm(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class ResetPasswordForm(BaseModel):
    token: str
    new_password: str
    confirm_new_password: str

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str) -> str:
        clean_token = value.strip()
        if not clean_token:
            raise ValueError("Reset token is required.")
        return clean_token

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        error = validate_strong_password(value, label="New password")
        if error:
            raise ValueError(error)
        return value

    @model_validator(mode="after")
    def validate_password_match(self) -> "ResetPasswordForm":
        error = validate_password_confirmation(
            self.new_password,
            self.confirm_new_password,
            label="New password",
        )
        if error:
            raise ValueError(error)
        return self
