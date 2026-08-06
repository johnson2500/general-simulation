"""Smoke test for GET /health.

The DB check is mocked so the test runs without a live Postgres instance.
"""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.health import get_settings
from src.core.config import Settings


def _test_settings() -> Settings:
    return Settings(
        postgres_dsn="postgresql://mock:mock@localhost:5432/mock",
    )


app.dependency_overrides[get_settings] = _test_settings


@pytest.fixture(autouse=True)
def _ready_pool():
    """Simulate a finished lifespan so readiness checks exercise the DB probe."""
    app.state.pool = object()
    yield
    app.state.pool = None


@pytest.mark.asyncio
async def test_health_db_reachable():
    with patch(
        "src.api.health._check_db",
        new_callable=AsyncMock,
        return_value=True,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "reachable"


@pytest.mark.asyncio
async def test_health_db_unreachable():
    with patch(
        "src.api.health._check_db",
        new_callable=AsyncMock,
        return_value=False,
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["db"] == "unreachable"


@pytest.mark.asyncio
async def test_health_pool_unavailable():
    app.state.pool = None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "starting"
    assert body["db"] == "pool_unavailable"


@pytest.mark.asyncio
async def test_livez_always_ok_when_pool_unavailable():
    app.state.pool = None
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/livez")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
