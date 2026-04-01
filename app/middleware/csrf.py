from __future__ import annotations

import secrets
import logging
from typing import Callable

from fastapi import HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.config import get_settings

CSRF_COOKIE_NAME = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
settings = get_settings()
logger = logging.getLogger(__name__)


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


async def validate_csrf(request: Request) -> None:
    if request.method.upper() in SAFE_METHODS:
        return

    if request.url.path.startswith("/static"):
        return

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not cookie_token:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing CSRF cookie")

    header_token = request.headers.get(CSRF_HEADER_NAME)
    if header_token and secrets.compare_digest(header_token, cookie_token):
        return

    form_token = None
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
        # Read body and cache it so FastAPI can read it later
        body = await request.body()
        # Create a new receive callable that replays the body
        async def receive():
            return {"type": "http.request", "body": body}
        request._receive = receive
        
        # Parse the form from the cached body
        form = await request.form()
        form_token = form.get(CSRF_FORM_FIELD)

    if not form_token or not secrets.compare_digest(str(form_token), cookie_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


async def csrf_dispatch(request: Request, call_next: Callable[[Request], Response]) -> Response:
    csrf_token = request.cookies.get(CSRF_COOKIE_NAME) or generate_csrf_token()
    request.state.csrf_token = csrf_token

    try:
        await validate_csrf(request)
    except HTTPException as exc:
        logger.warning(
            "csrf_validation_failed",
            extra={"path": request.url.path, "method": request.method, "detail": exc.detail},
        )
        response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        if request.cookies.get(CSRF_COOKIE_NAME) != csrf_token:
            response.set_cookie(
                key=CSRF_COOKIE_NAME,
                value=csrf_token,
                httponly=False,
                secure=not settings.debug,
                samesite="lax",
                max_age=60 * 60 * 24 * 7,
            )
        return response

    response = await call_next(request)

    if request.cookies.get(CSRF_COOKIE_NAME) != csrf_token:
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_token,
            httponly=False,
            secure=not settings.debug,
            samesite="lax",
            max_age=60 * 60 * 24 * 7,
        )
    return response
