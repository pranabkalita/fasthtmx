from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db_session
from app.db.models import AuditLog, User
from app.dependencies import get_admin_user
from app.templating import templates

router = APIRouter(prefix="/admin", tags=["admin"])


def _parse_optional_date(raw: str) -> date | None:
    value = raw.strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


@router.get("/audit-logs", response_class=HTMLResponse)
async def audit_logs(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=10, le=100),
    action: str = Query(default=""),
    target: str = Query(default=""),
    user_id: str = Query(default=""),
    ip_address: str = Query(default=""),
    from_date: str = Query(default=""),
    to_date: str = Query(default=""),
) -> HTMLResponse:
    conditions = []

    clean_action = action.strip()
    clean_target = target.strip()
    clean_user_id = user_id.strip()
    clean_ip_address = ip_address.strip()

    if clean_action:
        conditions.append(AuditLog.action.ilike(f"%{clean_action}%"))
    if clean_target:
        conditions.append(AuditLog.target.ilike(f"%{clean_target}%"))
    if clean_user_id:
        conditions.append(AuditLog.user_id.ilike(f"%{clean_user_id}%"))
    if clean_ip_address:
        conditions.append(AuditLog.ip_address.ilike(f"%{clean_ip_address}%"))
    parsed_from_date = _parse_optional_date(from_date)
    parsed_to_date = _parse_optional_date(to_date)

    if parsed_from_date is not None:
        conditions.append(AuditLog.created_at >= datetime.combine(parsed_from_date, time.min))
    if parsed_to_date is not None:
        conditions.append(AuditLog.created_at <= datetime.combine(parsed_to_date, time.max))

    total_stmt = select(func.count()).select_from(AuditLog)
    if conditions:
        total_stmt = total_stmt.where(*conditions)
    total_items = (await db.execute(total_stmt)).scalar_one()

    total_pages = max((total_items + per_page - 1) // per_page, 1)
    current_page = min(page, total_pages)

    rows_stmt = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .offset((current_page - 1) * per_page)
        .limit(per_page)
    )
    if conditions:
        rows_stmt = rows_stmt.where(*conditions)

    rows = (await db.execute(rows_stmt)).scalars().all()

    context = {
        "title": "Audit Logs",
        "logs": rows,
        "user": current_user,
        "page": current_page,
        "per_page": per_page,
        "total_items": total_items,
        "total_pages": total_pages,
        "has_prev": current_page > 1,
        "has_next": current_page < total_pages,
        "filters": {
            "action": clean_action,
            "target": clean_target,
            "user_id": clean_user_id,
            "ip_address": clean_ip_address,
            "from_date": parsed_from_date,
            "to_date": parsed_to_date,
        },
    }

    is_htmx = request.headers.get("HX-Request") == "true"
    hx_target = request.headers.get("HX-Target", "")
    if is_htmx and hx_target == "audit-logs-panel":
        return templates.TemplateResponse(
            request,
            "dashboard/_audit_logs_panel.html",
            context,
        )

    return templates.TemplateResponse(
        request,
        "dashboard/audit_logs.html",
        context,
    )
