"""Tests for GET /admin/entities/geojson."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.deps import get_pool


def _pool(conn: AsyncMock) -> MagicMock:
    pool = MagicMock()
    pool.fetch = conn.fetch
    return pool


@pytest.fixture()
def override_pool():
    conn = AsyncMock()
    pool = _pool(conn)
    app.dependency_overrides[get_pool] = lambda: pool
    yield conn
    app.dependency_overrides.pop(get_pool, None)


@pytest.mark.asyncio
async def test_entities_geojson_feature_collection(override_pool: AsyncMock):
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    override_pool.fetch.return_value = [
        {
            "id": "opensky-407290",
            "type": "moving_entity",
            "attributes": '{"call_sign": "BAW442"}',
            "updated_at": now,
            "geojson": '{"type":"Point","coordinates":[-0.45,51.47]}',
            "latest_status": "airborne",
        },
        {
            "id": "no-geo",
            "type": "moving_entity",
            "attributes": "{}",
            "updated_at": now,
            "geojson": None,
            "latest_status": None,
        },
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/admin/entities/geojson")

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 1
    feature = body["features"][0]
    assert feature["id"] == "opensky-407290"
    assert feature["geometry"]["type"] == "Point"
    assert feature["geometry"]["coordinates"] == [-0.45, 51.47]
    assert feature["properties"]["status"] == "airborne"
    assert feature["properties"]["attributes"]["call_sign"] == "BAW442"


@pytest.mark.asyncio
async def test_entities_geojson_invalid_bbox(override_pool: AsyncMock):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/admin/entities/geojson?bbox=1,2,3")

    assert response.status_code == 422
    override_pool.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_entities_geojson_ids_filter(override_pool: AsyncMock):
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    override_pool.fetch.return_value = [
        {
            "id": "opensky-407290",
            "type": "moving_entity",
            "attributes": "{}",
            "updated_at": now,
            "geojson": '{"type":"Point","coordinates":[-0.55,51.48]}',
            "latest_status": "airborne",
        },
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/admin/entities/geojson?ids=opensky-407290,opensky-471f52"
        )

    assert response.status_code == 200
    assert len(response.json()["features"]) == 1
    # First SQL arg after filters should be the id list.
    args = override_pool.fetch.await_args.args
    assert ["opensky-407290", "opensky-471f52"] in args
