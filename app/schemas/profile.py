from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator

from app.schemas._common import normalize_email
from app.services.password_policy import validate_password_confirmation, validate_strong_password


class ProfileUpdateForm(BaseModel):
    full_name: str = ""
    email: str

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, value: str) -> str:
        clean_name = value.strip()
        if len(clean_name) > 120:
            raise ValueError("Name must be 120 characters or fewer.")
        return clean_name

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class ChangePasswordForm(BaseModel):
    current_password: str
    new_password: str
    confirm_new_password: str

    @field_validator("current_password")
    @classmethod
    def validate_current_password(cls, value: str) -> str:
        clean_password = value.strip()
        if not clean_password:
            raise ValueError("Current password is invalid.")
        return value

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        error = validate_strong_password(value, label="New password")
        if error:
            raise ValueError(error)
        return value

    @model_validator(mode="after")
    def validate_password_match(self) -> "ChangePasswordForm":
        error = validate_password_confirmation(
            self.new_password,
            self.confirm_new_password,
            label="New password",
        )
        if error:
            raise ValueError(error)
        return self
