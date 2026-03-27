from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings

settings = get_settings()

TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "templates"
email_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_ROOT)),
    autoescape=select_autoescape(["html", "xml"]),
)

mail_conf = ConnectionConfig(
    MAIL_USERNAME=settings.mail_username,
    MAIL_PASSWORD=settings.mail_password,
    MAIL_FROM=settings.mail_from,
    MAIL_PORT=settings.mail_port,
    MAIL_SERVER=settings.mail_server,
    MAIL_FROM_NAME=settings.mail_from_name,
    MAIL_STARTTLS=settings.mail_starttls,
    MAIL_SSL_TLS=settings.mail_ssl_tls,
    USE_CREDENTIALS=True,
)

mailer = FastMail(mail_conf)


def _default_email_context() -> dict[str, Any]:
    return {
        "product_name": settings.app_name,
        "website_url": settings.app_url,
        "support_email": settings.mail_from,
        "year": datetime.now(UTC).year,
    }


def render_email_bodies(template_name: str, context: dict[str, Any]) -> tuple[str, str]:
    merged_context = _default_email_context() | context
    html_template = email_env.get_template(f"emails/{template_name}.html")
    text_template = email_env.get_template(f"emails/text/{template_name}.txt")
    return html_template.render(**merged_context), text_template.render(**merged_context)


async def send_email(
    subject: str,
    recipients: list[str],
    html_body: str,
    text_body: str | None = None,
) -> None:
    message = MessageSchema(
        subject=subject,
        recipients=recipients,
        body=html_body,
        alternative_body=text_body,
        subtype=MessageType.html,
        multipart_subtype="alternative",
    )
    await mailer.send_message(message)


async def send_templated_email(
    subject: str,
    recipients: list[str],
    template_name: str,
    context: dict[str, Any],
) -> None:
    html_body, text_body = render_email_bodies(template_name=template_name, context=context)
    await send_email(subject=subject, recipients=recipients, html_body=html_body, text_body=text_body)
