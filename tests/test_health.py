from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app


def test_queue_health_endpoint_reports_ok():
    with patch("app.main.is_job_queue_healthy", new_callable=AsyncMock, return_value=True):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/healthz/queue")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "job_queue"}


def test_queue_health_endpoint_reports_unhealthy():
    with patch("app.main.is_job_queue_healthy", new_callable=AsyncMock, return_value=False):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/healthz/queue")

    assert response.status_code == 503
    assert response.json() == {"ok": False, "service": "job_queue"}


def test_security_headers_present():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers.get("x-frame-options") == "DENY"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("referrer-policy") == "strict-origin-when-cross-origin"
