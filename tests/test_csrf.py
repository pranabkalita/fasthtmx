from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.middleware.csrf import csrf_dispatch


app = FastAPI()
app.middleware("http")(csrf_dispatch)


@app.get("/ping")
async def ping() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.post("/submit")
async def submit() -> JSONResponse:
    return JSONResponse({"ok": True})


def test_sets_csrf_cookie_on_get() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/ping")
    assert response.status_code == 200
    assert "csrf_token" in response.cookies


def test_rejects_post_without_token() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/ping")
    response = client.post("/submit", data={})
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid CSRF token"


def test_accepts_post_with_form_csrf_token() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    get_response = client.get("/ping")
    token = get_response.cookies.get("csrf_token")
    response = client.post("/submit", data={"csrf_token": token})
    assert response.status_code == 200
