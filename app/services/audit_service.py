from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


async def write_audit_log(
    db: AsyncSession,
    action: str,
    target: str = "system",
    details: str = "",
    request: Request | None = None,
    user_id: str | None = None,
) -> None:
    ip = request.client.host if request and request.client else None
    user_agent = request.headers.get("user-agent") if request else None
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            target=target,
            details=details,
            ip_address=ip,
            user_agent=user_agent,
        )
    )
    await db.commit()
