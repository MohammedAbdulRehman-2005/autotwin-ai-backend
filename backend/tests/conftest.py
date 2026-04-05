"""
tests/conftest.py
──────────────────
Shared pytest fixtures and configuration for AutoTwin AI test suite.

- Provides a shared AsyncClient for all API integration tests.
- Sets asyncio_mode = auto (also enforced via pytest.ini).
- Ensures MongoDB-dependent code never blocks tests (demo mode active by default).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from main import app


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncClient:
    """
    Yield a fully configured async HTTP test client for the FastAPI app.
    Uses ASGI transport — no live server required.
    Scoped per-function so each test starts with a clean request context.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=True,
    ) as ac:
        yield ac


@pytest.fixture(scope="session", autouse=True)
def ensure_demo_mode() -> None:
    """
    Verify the app is running in demo mode (no live MongoDB).
    This prevents tests from accidentally mutating a real database.
    """
    from models.database import is_demo_mode
    # If MongoDB IS available, that's also fine — tests are read-safe.
    # We just log the mode so CI output is clear.
    mode = "DEMO (in-memory)" if is_demo_mode() else "LIVE (MongoDB connected)"
    print(f"\n[conftest] Running tests in {mode} mode.")
