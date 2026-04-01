"""
Pytest configuration and shared fixtures for integration tests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

settings = None


@pytest.fixture(scope="session", autouse=True)
def configure_settings():
    """Configure settings at session start."""
    global settings
    from app.config import get_settings
    settings = get_settings()


@pytest.fixture
def mock_redis():
    """Create a mock Redis client for rate limiting."""
    # Create a simple in-memory store for rate limiting
    store = {}
    
    class MockPipeline:
        def __init__(self, store):
            self.store = store
            self.ops = []
        
        def incr(self, key):
            self.ops.append(('incr', key))
        
        def expire(self, key, ttl, nx=False):
            self.ops.append(('expire', key, ttl, nx))
        
        async def execute(self):
            # Process operations
            result = []
            for op in self.ops:
                if op[0] == 'incr':
                    key = op[1]
                    self.store[key] = self.store.get(key, 0) + 1
                    result.append(self.store[key])
            # Return [current_count, True] format expected by rate limiter
            return result if result else [1, True]
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
    
    class MockRedis:
        def __init__(self):
            self.store = store
        
        def pipeline(self, transaction=True):
            return MockPipeline(self.store)
        
        async def aclose(self):
            pass
    
    return MockRedis()



@pytest.fixture
def mock_send_email():
    """Mock email sender functions to avoid actual email sending."""
    with patch("app.services.email_service.send_email", new_callable=AsyncMock) as mock_plain:
        with patch("app.services.email_service.send_templated_email", new_callable=AsyncMock) as mock_templated:
            yield {
                "send_email": mock_plain,
                "send_templated_email": mock_templated,
            }


@pytest.fixture
def mock_email_queue():
    """Mock queued email dispatch in route handlers."""
    with patch("app.routers.auth_public.enqueue_templated_email", new_callable=AsyncMock) as auth_public_queue:
        with patch("app.routers.auth_recovery.enqueue_templated_email", new_callable=AsyncMock) as auth_recovery_queue:
            with patch("app.routers.profile.enqueue_templated_email", new_callable=AsyncMock) as dashboard_queue:
                yield {
                    "auth_public": auth_public_queue,
                    "auth_recovery": auth_recovery_queue,
                    "dashboard": dashboard_queue,
                }


@pytest.fixture
def test_client(mock_redis, mock_send_email, mock_email_queue):
    """Create a test client with mocked dependencies."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from fastapi.staticfiles import StaticFiles
    from starlette.middleware.sessions import SessionMiddleware
    from app.cache import get_redis
    from app.db.database import get_db_session, AsyncSessionLocal
    from app.config import get_settings
    from app.routers import audit, auth, dashboard, email, profile, queue, sessions
    
    settings_local = get_settings()
    
    # Create a test app without CSRF middleware (middleware consumes body, breaks form parsing)
    test_app = FastAPI(title=settings_local.app_name, debug=settings_local.debug)
    test_app.add_middleware(
        SessionMiddleware,
        secret_key=settings_local.secret_key,
        same_site="lax",
        https_only=not settings_local.debug,
    )
    
    # Mount static files
    test_app.mount("/static", StaticFiles(directory="static"), name="static")
    
    # Include routers
    test_app.include_router(auth.router)
    test_app.include_router(dashboard.router)
    test_app.include_router(profile.router)
    test_app.include_router(sessions.router)
    test_app.include_router(audit.router)
    test_app.include_router(email.router)
    test_app.include_router(queue.router)
    
    # Add healthz endpoint
    from fastapi.responses import HTMLResponse, JSONResponse
    from app.services.job_queue import is_job_queue_healthy
    
    @test_app.get("/healthz", response_class=HTMLResponse)
    async def healthz(request):
        return HTMLResponse("ok")

    @test_app.get("/healthz/queue")
    async def queue_healthz(_: Request):
        healthy = await is_job_queue_healthy()
        status_code = 200 if healthy else 503
        return JSONResponse({"ok": healthy, "service": "job_queue"}, status_code=status_code)
    
    # Override dependencies
    async def override_get_db():
        async with AsyncSessionLocal() as session:
            yield session
    
    async def override_get_redis():
        return mock_redis
    
    test_app.dependency_overrides[get_db_session] = override_get_db
    test_app.dependency_overrides[get_redis] = override_get_redis
    
    try:
        with TestClient(test_app, raise_server_exceptions=False) as client:
            yield client
    finally:
        test_app.dependency_overrides.clear()


@pytest.fixture
def test_client_with_csrf(test_client):
    """Ensure test client has CSRF token set."""
    from app.config import get_settings
    
    settings_local = get_settings()
    
    # Get a page that sets CSRF token
    response = test_client.get("/")
    assert response.status_code == 200
    csrf_token = response.cookies.get("csrf_token")
    if not csrf_token:
        # If no CSRF token from cookie, we might need to fallback
        csrf_token = "test_token"
    
    return test_client, csrf_token
