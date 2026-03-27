from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(alias="APP_NAME")
    app_url: str = Field(alias="APP_URL")
    debug: bool = Field(alias="DEBUG", default=False)
    secret_key: str = Field(alias="SECRET_KEY")

    db_host: str = Field(alias="DB_HOST")
    db_port: int = Field(alias="DB_PORT")
    db_name: str = Field(alias="DB_NAME")
    db_user: str = Field(alias="DB_USER")
    db_password: str = Field(alias="DB_PASSWORD")

    redis_host: str = Field(alias="REDIS_HOST")
    redis_port: int = Field(alias="REDIS_PORT")
    redis_db: int = Field(alias="REDIS_DB")
    redis_password: str | None = Field(alias="REDIS_PASSWORD", default=None)

    session_cookie_name: str = Field(alias="SESSION_COOKIE_NAME", default="session_id")
    session_max_age: int = Field(alias="SESSION_MAX_AGE", default=604800)

    mail_username: str = Field(alias="MAIL_USERNAME")
    mail_password: str = Field(alias="MAIL_PASSWORD")
    mail_from: str = Field(alias="MAIL_FROM")
    mail_from_name: str = Field(alias="MAIL_FROM_NAME")
    mail_server: str = Field(alias="MAIL_SERVER")
    mail_port: int = Field(alias="MAIL_PORT")
    mail_starttls: bool = Field(alias="MAIL_STARTTLS", default=True)
    mail_ssl_tls: bool = Field(alias="MAIL_SSL_TLS", default=False)

    login_max_attempts: int = Field(alias="LOGIN_MAX_ATTEMPTS", default=5)
    login_lockout_minutes: int = Field(alias="LOGIN_LOCKOUT_MINUTES", default=15)
    account_purge_days: int = Field(alias="ACCOUNT_PURGE_DAYS", default=30)

    @property
    def database_url(self) -> str:
        return (
            f"mysql+asyncmy://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return (
                f"redis://:{self.redis_password}@{self.redis_host}:"
                f"{self.redis_port}/{self.redis_db}"
            )
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
