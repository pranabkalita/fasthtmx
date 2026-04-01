from __future__ import annotations

from collections.abc import Callable

from fastapi import Request, Response

from app.config import get_settings

settings = get_settings()


def _csp_value() -> str:
    # Tight policy for self-hosted frontend assets.
    return (
        "default-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; "
        "font-src 'self' data:"
    )


async def security_headers_dispatch(
    request: Request, call_next: Callable[[Request], Response]
) -> Response:
    response = await call_next(request)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if not settings.debug:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if settings.csp_enabled:
        header = "Content-Security-Policy-Report-Only" if settings.csp_report_only else "Content-Security-Policy"
        response.headers.setdefault(header, _csp_value())
    return response
