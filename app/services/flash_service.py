from __future__ import annotations

from fastapi import Request


def add_toast(
    request: Request,
    *,
    message: str,
    type: str = "info",
    duration: int = 4200,
) -> None:
    if not message.strip():
        return

    try:
        session = request.session
    except Exception:
        return

    toasts = session.get("_toasts", [])
    toasts.append(
        {
            "message": message,
            "type": type,
            "duration": duration,
        }
    )
    session["_toasts"] = toasts
