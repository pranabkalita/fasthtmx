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
    session_idle_timeout_seconds: int = Field(alias="SESSION_IDLE_TIMEOUT_SECONDS", default=1800)
    session_absolute_timeout_seconds: int = Field(alias="SESSION_ABSOLUTE_TIMEOUT_SECONDS", default=43200)
    session_renewal_threshold_seconds: int = Field(alias="SESSION_RENEWAL_THRESHOLD_SECONDS", default=300)
    remember_me_enabled: bool = Field(alias="REMEMBER_ME_ENABLED", default=False)
    remember_me_idle_timeout_seconds: int = Field(alias="REMEMBER_ME_IDLE_TIMEOUT_SECONDS", default=86400)
    remember_me_absolute_timeout_seconds: int = Field(
        alias="REMEMBER_ME_ABSOLUTE_TIMEOUT_SECONDS", default=604800
    )
    step_up_window_seconds: int = Field(alias="STEP_UP_WINDOW_SECONDS", default=900)
    reset_token_expiry_minutes: int = Field(alias="RESET_TOKEN_EXPIRY_MINUTES", default=30)

    mail_username: str = Field(alias="MAIL_USERNAME")
    mail_password: str = Field(alias="MAIL_PASSWORD")
    mail_from: str = Field(alias="MAIL_FROM")
    mail_from_name: str = Field(alias="MAIL_FROM_NAME")
    mail_server: str = Field(alias="MAIL_SERVER")
    mail_port: int = Field(alias="MAIL_PORT")
    mail_starttls: bool = Field(alias="MAIL_STARTTLS", default=True)
    mail_ssl_tls: bool = Field(alias="MAIL_SSL_TLS", default=False)

    login_max_attempts: int = Field(alias="LOGIN_MAX_ATTEMPTS", default=5)
    login_max_attempts_per_ip: int = Field(alias="LOGIN_MAX_ATTEMPTS_PER_IP", default=5)
    login_max_attempts_account_window: int = Field(alias="LOGIN_MAX_ATTEMPTS_ACCOUNT_WINDOW", default=20)
    login_lockout_minutes: int = Field(alias="LOGIN_LOCKOUT_MINUTES", default=15)
    account_purge_days: int = Field(alias="ACCOUNT_PURGE_DAYS", default=30)
    trusted_proxy_ips: str = Field(alias="TRUSTED_PROXY_IPS", default="")
    use_forwarded_headers: bool = Field(alias="USE_FORWARDED_HEADERS", default=False)
    csp_enabled: bool = Field(alias="CSP_ENABLED", default=True)
    csp_report_only: bool = Field(alias="CSP_REPORT_ONLY", default=False)

    @property
    def trusted_proxy_ip_set(self) -> set[str]:
        return {part.strip() for part in self.trusted_proxy_ips.split(",") if part.strip()}

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
