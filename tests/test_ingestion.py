"""Smoke tests for the ingestion framework.

No network calls and no live database — everything is mocked/stubbed.

Coverage:
  - CanonicalEntity Protocol conformance
  - USGSEarthquakeAdapter.normalize() against a recorded fixture
  - Runner upserts (mocked asyncpg connection)
  - Ingestion tool schema + callable dispatch
  - Bad fixture rows are silently skipped
"""
from __future__ import annotations

import json
from datetime import timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.ingestion import CanonicalEntity, IngestionAdapter
from src.core.config import Settings
from domain.earthquakes.adapters.usgs_earthquakes import (
    ENTITY_TYPE,
    USGSEarthquakeAdapter,
)
from src.ingestion.registry import list_adapter_ids
from src.ingestion.runner import _insert_state, _upsert_entity, run_ingestion
from src.ingestion.tool import (
    call_ingestion_tool,
    get_ingestion_tool_schema,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Tests that exercise the registry / tool enum need both demo domains loaded.
_BOTH_DOMAINS = Settings(enabled_domains="aviation,earthquakes")



# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_pool_mock() -> MagicMock:
    """asyncpg.Pool mock whose acquire() returns an async context manager."""
    conn = AsyncMock()
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


# ── CanonicalEntity ───────────────────────────────────────────────────────────


def test_canonical_entity_protocol():
    from datetime import datetime
    entity = CanonicalEntity(
        id="test-1", type="moving_entity",
        timestamp=datetime.now(tz=timezone.utc),
        status="reviewed",
        geometry={"type": "Point", "coordinates": [0.0, 0.0]},
        attributes={"key": "value"},
    )
    assert entity.id == "test-1"
    assert entity.geometry is not None
    assert isinstance(entity.attributes, dict)


def test_canonical_entity_defaults():
    from datetime import datetime
    entity = CanonicalEntity(
        id="min", type="t",
        timestamp=datetime.now(tz=timezone.utc),
        status="ok",
    )
    assert entity.geometry is None
    assert entity.attributes == {}


# ── USGSEarthquakeAdapter.normalize() ─────────────────────────────────────────


def test_normalize_returns_correct_count():
    raw = _load_fixture("usgs_earthquakes.json")
    adapter = USGSEarthquakeAdapter()
    entities = adapter.normalize(raw)
    # 4 features in fixture; 1 has null time → should be skipped
    assert len(entities) == 3


def test_normalize_entity_ids_prefixed():
    raw = _load_fixture("usgs_earthquakes.json")
    entities = USGSEarthquakeAdapter().normalize(raw)
    for e in entities:
        assert e.id.startswith("usgs-"), f"Expected 'usgs-' prefix, got: {e.id}"


def test_normalize_entity_type():
    raw = _load_fixture("usgs_earthquakes.json")
    entities = USGSEarthquakeAdapter().normalize(raw)
    assert all(e.type == ENTITY_TYPE for e in entities)


def test_normalize_geometry_is_point():
    raw = _load_fixture("usgs_earthquakes.json")
    entities = USGSEarthquakeAdapter().normalize(raw)
    for e in entities:
        assert e.geometry is not None
        assert e.geometry["type"] == "Point"
        lon, lat = e.geometry["coordinates"]
        assert -180 <= lon <= 180
        assert -90 <= lat <= 90


def test_normalize_timestamp_is_utc():
    raw = _load_fixture("usgs_earthquakes.json")
    entities = USGSEarthquakeAdapter().normalize(raw)
    for e in entities:
        assert e.timestamp.tzinfo is not None
        assert e.timestamp.tzinfo == timezone.utc


def test_normalize_status_values():
    raw = _load_fixture("usgs_earthquakes.json")
    entities = USGSEarthquakeAdapter().normalize(raw)
    statuses = {e.status for e in entities}
    assert statuses == {"reviewed", "automatic"}


def test_normalize_attributes_contain_magnitude():
    raw = _load_fixture("usgs_earthquakes.json")
    entities = USGSEarthquakeAdapter().normalize(raw)
    for e in entities:
        assert "magnitude" in e.attributes
        assert isinstance(e.attributes["magnitude"], (int, float))


def test_normalize_skips_bad_feature():
    raw = _load_fixture("usgs_earthquakes.json")
    entities = USGSEarthquakeAdapter().normalize(raw)
    ids = {e.id for e in entities}
    assert "usgs-bad-no-time" not in ids


def test_normalize_empty_features():
    entities = USGSEarthquakeAdapter().normalize({"features": []})
    assert entities == []


# ── fetch() is mocked so no network call ─────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_returns_parsed_json():
    fixture = _load_fixture("usgs_earthquakes.json")
    adapter = USGSEarthquakeAdapter()
    with patch.object(adapter, "fetch", new_callable=AsyncMock, return_value=fixture):
        raw = await adapter.fetch()
    assert raw["type"] == "FeatureCollection"
    assert "features" in raw


# ── Runner upserts ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_ingestion_calls_upsert_for_each_entity():
    fixture = _load_fixture("usgs_earthquakes.json")
    adapter = USGSEarthquakeAdapter()
    pool, conn = _make_pool_mock()

    with patch.object(adapter, "fetch", new_callable=AsyncMock, return_value=fixture):
        count = await run_ingestion(adapter, pool)

    assert count == 3  # 3 valid features in fixture
    # Each entity → 1 upsert + 1 state insert = 2 execute calls × 3 entities = 6
    assert conn.execute.await_count == 6


@pytest.mark.asyncio
async def test_run_ingestion_returns_zero_for_empty_source():
    adapter = USGSEarthquakeAdapter()
    pool, conn = _make_pool_mock()

    with patch.object(adapter, "fetch", new_callable=AsyncMock, return_value={"features": []}):
        count = await run_ingestion(adapter, pool)

    assert count == 0
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_entity_executes_sql():
    from datetime import datetime
    conn = AsyncMock()
    entity = CanonicalEntity(
        id="e1", type="moving_entity",
        timestamp=datetime.now(tz=timezone.utc),
        status="reviewed",
        geometry={"type": "Point", "coordinates": [-117.6, 35.7]},
        attributes={"magnitude": 2.5},
    )
    await _upsert_entity(conn, entity)
    conn.execute.assert_awaited_once()
    sql, eid, etype, geo_arg, _ = conn.execute.call_args.args
    assert "INSERT INTO entity" in sql
    assert "ON CONFLICT" in sql
    assert eid == "e1"
    assert etype == "moving_entity"
    assert "-117.6" in geo_arg  # geo_json


@pytest.mark.asyncio
async def test_upsert_entity_none_geometry():
    from datetime import datetime
    conn = AsyncMock()
    entity = CanonicalEntity(
        id="e2", type="t",
        timestamp=datetime.now(tz=timezone.utc),
        status="s",
        geometry=None,
    )
    await _upsert_entity(conn, entity)
    _, _, _, geo_arg, _ = conn.execute.call_args.args
    assert geo_arg is None


@pytest.mark.asyncio
async def test_insert_state_executes_sql():
    from datetime import datetime
    conn = AsyncMock()
    entity = CanonicalEntity(
        id="e3", type="t",
        timestamp=datetime.now(tz=timezone.utc),
        status="reviewed",
    )
    await _insert_state(conn, entity)
    conn.execute.assert_awaited_once()
    sql, entity_id, status, *_ = conn.execute.call_args.args
    assert "INSERT INTO entity_state" in sql
    assert entity_id == "e3"
    assert status == "reviewed"


# ── Ingestion tool ────────────────────────────────────────────────────────────


def test_tool_schema_shape():
    schema = get_ingestion_tool_schema(_BOTH_DOMAINS)
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "run_ingestion_pull"
    assert "adapter_id" in fn["parameters"]["properties"]
    assert "adapter_id" in fn["parameters"]["required"]


def test_tool_schema_adapter_enum_matches_registry():
    schema = get_ingestion_tool_schema(_BOTH_DOMAINS)
    enum_values = set(
        schema["function"]["parameters"]["properties"]["adapter_id"]["enum"]
    )
    assert enum_values == set(list_adapter_ids(_BOTH_DOMAINS))
    assert enum_values == {"opensky_flights", "usgs_earthquakes"}


@pytest.mark.asyncio
async def test_call_ingestion_tool_success():
    # The tool dispatches to run_ingestion — test that dispatch, not ingestion
    # internals (which have their own tests above).  Mock run_ingestion directly
    # so no real network or DB calls happen.
    pool, _ = _make_pool_mock()

    with patch(
        "src.ingestion.tool.run_ingestion",
        new_callable=AsyncMock,
        return_value=3,
    ):
        result = await call_ingestion_tool(
            {"adapter_id": "usgs_earthquakes"},
            pool,
            settings=_BOTH_DOMAINS,
        )

    assert result["success"] is True
    assert result["adapter_id"] == "usgs_earthquakes"
    assert result["entities_upserted"] == 3


@pytest.mark.asyncio
async def test_call_ingestion_tool_unknown_adapter():
    pool, _ = _make_pool_mock()
    result = await call_ingestion_tool(
        {"adapter_id": "nonexistent"},
        pool,
        settings=_BOTH_DOMAINS,
    )
    assert result["success"] is False
    assert "nonexistent" in result["error"]


@pytest.mark.asyncio
async def test_call_ingestion_tool_disabled_domain():
    """Adapter from a domain not in ENABLED_DOMAINS is rejected."""
    pool, _ = _make_pool_mock()
    aviation_only = Settings(enabled_domains="aviation")
    result = await call_ingestion_tool(
        {"adapter_id": "usgs_earthquakes"},
        pool,
        settings=aviation_only,
    )
    assert result["success"] is False
    assert "usgs_earthquakes" in result["error"]


# ── IngestionAdapter Protocol conformance ─────────────────────────────────────


def test_usgs_adapter_satisfies_protocol():
    adapter = USGSEarthquakeAdapter()
    assert isinstance(adapter, IngestionAdapter)
    assert adapter.adapter_id == "usgs_earthquakes"
