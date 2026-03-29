from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.cache import redis_client
from app.config import get_settings
from app.middleware.csrf import csrf_dispatch
from app.routers import admin_tools, audit, auth, dashboard, security
from app.services.job_queue import close_job_queue, is_job_queue_healthy
from app.templating import templates

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_job_queue()
    await redis_client.aclose()


app = FastAPI(title=settings.app_name, debug=settings.debug, lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=not settings.debug,
)
app.middleware("http")(csrf_dispatch)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(audit.router)
app.include_router(security.router)
app.include_router(admin_tools.router)


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


@app.get("/healthz/queue")
async def queue_healthz(_: Request) -> JSONResponse:
    healthy = await is_job_queue_healthy()
    status_code = 200 if healthy else 503
    return JSONResponse({"ok": healthy, "service": "job_queue"}, status_code=status_code)
