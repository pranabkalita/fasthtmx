from __future__ import annotations

from starlette.templating import Jinja2Templates


def _flash_context(request) -> dict[str, list[dict[str, object]]]:
    flashes: list[dict[str, object]] = []
    try:
        session = request.session
        flashes = session.pop("_toasts", [])
    except Exception:
        flashes = []
    return {"flash_toasts": flashes}


templates = Jinja2Templates(
    directory="templates",
    context_processors=[_flash_context],
)
