from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from app.cache import redis_client
from app.config import get_settings
from app.middleware.csrf import csrf_dispatch
from app.routers import audit, auth, dashboard

settings = get_settings()
templates = Jinja2Templates(directory="templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await redis_client.aclose()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.middleware("http")(csrf_dispatch)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(audit.router)


@app.exception_handler(HTTPException)
async def app_http_exception_handler(request: Request, exc: HTTPException):
    accept_header = request.headers.get("accept", "")
    wants_html = "text/html" in accept_header

    if exc.status_code == 401 and wants_html and request.method == "GET":
        return RedirectResponse(url="/login", status_code=303)

    if exc.status_code == 403 and wants_html and request.method == "GET":
        return templates.TemplateResponse(
            request, "errors/403.html", {}, status_code=403
        )

    return await http_exception_handler(request, exc)


@app.get("/healthz", response_class=HTMLResponse)
async def healthz(_: Request) -> HTMLResponse:
    return HTMLResponse("ok")
