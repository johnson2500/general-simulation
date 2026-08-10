"""Smoke tests for the ingestion framework.

No network calls and no live database — everything is mocked/stubbed.

Coverage:
  - CanonicalEntity Protocol conformance
  - ShippingDemoAdapter.normalize() against a recorded fixture
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

from domain.shipping.adapters.shipping_demo import (
    CARGO_TYPE,
    PORT_TYPE,
    VESSEL_TYPE,
    ShippingDemoAdapter,
)
from src.core.config import Settings
from src.core.ingestion import CanonicalEntity, IngestionAdapter
from src.ingestion.registry import list_adapter_ids
from src.ingestion.runner import _insert_state, _upsert_entity, run_ingestion
from src.ingestion.tool import (
    call_ingestion_tool,
    get_ingestion_tool_schema,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Tests that exercise the registry / tool enum need both shipped domains loaded.
_BOTH_DOMAINS = Settings(enabled_domains="aviation,shipping")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_pool_mock() -> tuple[MagicMock, AsyncMock]:
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
        id="test-1",
        type="moving_entity",
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
        id="min",
        type="t",
        timestamp=datetime.now(tz=timezone.utc),
        status="ok",
    )
    assert entity.geometry is None
    assert entity.attributes == {}


# ── ShippingDemoAdapter.normalize() ───────────────────────────────────────────


def test_normalize_returns_correct_count():
    raw = _load_fixture("shipping_demo.json")
    entities = ShippingDemoAdapter().normalize(raw)
    # 6 ports + 6 valid vessels + 10 valid cargo; 1 vessel + 1 cargo skipped
    assert len(entities) == 22


def test_normalize_entity_ids():
    raw = _load_fixture("shipping_demo.json")
    entities = ShippingDemoAdapter().normalize(raw)
    ids = {e.id for e in entities}
    assert "port-us-lax" in ids
    assert "vessel-ever-green-01" in ids
    assert "cargo-ever-green-01-1" in ids
    assert "vessel-bad-no-coords" not in ids
    assert "cargo-bad-no-carrier" not in ids


def test_normalize_entity_types():
    raw = _load_fixture("shipping_demo.json")
    entities = ShippingDemoAdapter().normalize(raw)
    by_type = {}
    for e in entities:
        by_type.setdefault(e.type, 0)
        by_type[e.type] += 1
    assert by_type[PORT_TYPE] == 6
    assert by_type[VESSEL_TYPE] == 6
    assert by_type[CARGO_TYPE] == 10


def test_normalize_vessel_geometry_is_point():
    raw = _load_fixture("shipping_demo.json")
    vessels = [
        e
        for e in ShippingDemoAdapter().normalize(raw)
        if e.type == VESSEL_TYPE
    ]
    for e in vessels:
        assert e.geometry is not None
        assert e.geometry["type"] == "Point"
        lon, lat = e.geometry["coordinates"]
        assert -180 <= lon <= 180
        assert -90 <= lat <= 90


def test_normalize_cargo_has_no_geometry():
    raw = _load_fixture("shipping_demo.json")
    cargo = [
        e
        for e in ShippingDemoAdapter().normalize(raw)
        if e.type == CARGO_TYPE
    ]
    assert cargo
    assert all(e.geometry is None for e in cargo)


def test_normalize_timestamp_is_utc():
    raw = _load_fixture("shipping_demo.json")
    entities = ShippingDemoAdapter().normalize(raw)
    for e in entities:
        assert e.timestamp.tzinfo is not None
        assert e.timestamp.tzinfo == timezone.utc


def test_normalize_cargo_value_usd():
    raw = _load_fixture("shipping_demo.json")
    cargo = {
        e.id: e
        for e in ShippingDemoAdapter().normalize(raw)
        if e.type == CARGO_TYPE
    }
    item = cargo["cargo-ever-green-01-1"]
    assert item.attributes["value_usd"] == 400 * 1200


def test_normalize_empty_payload():
    entities = ShippingDemoAdapter().normalize(
        {"ports": [], "vessels": [], "cargo": []}
    )
    assert entities == []


# ── fetch() is offline (fixture file) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_returns_parsed_json():
    adapter = ShippingDemoAdapter()
    raw = await adapter.fetch()
    assert "ports" in raw
    assert "vessels" in raw
    assert "cargo" in raw


# ── Runner upserts ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_ingestion_calls_upsert_for_each_entity():
    fixture = _load_fixture("shipping_demo.json")
    adapter = ShippingDemoAdapter()
    pool, conn = _make_pool_mock()

    with patch.object(adapter, "fetch", new_callable=AsyncMock, return_value=fixture):
        count = await run_ingestion(adapter, pool)

    assert count == 22
    # Each entity → 1 upsert + 1 state insert = 2 execute calls × 22 = 44
    assert conn.execute.await_count == 44


@pytest.mark.asyncio
async def test_run_ingestion_returns_zero_for_empty_source():
    adapter = ShippingDemoAdapter()
    pool, conn = _make_pool_mock()

    empty = {"ports": [], "vessels": [], "cargo": []}
    with patch.object(adapter, "fetch", new_callable=AsyncMock, return_value=empty):
        count = await run_ingestion(adapter, pool)

    assert count == 0
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_upsert_entity_executes_sql():
    from datetime import datetime

    conn = AsyncMock()
    entity = CanonicalEntity(
        id="e1",
        type="moving_entity",
        timestamp=datetime.now(tz=timezone.utc),
        status="underway",
        geometry={"type": "Point", "coordinates": [-118.26, 33.74]},
        attributes={"revenue_usd": 1000},
    )
    await _upsert_entity(conn, entity)
    conn.execute.assert_awaited_once()
    sql, eid, etype, geo_arg, _ = conn.execute.call_args.args
    assert "INSERT INTO entity" in sql
    assert "ON CONFLICT" in sql
    assert eid == "e1"
    assert etype == "moving_entity"
    assert "-118.26" in geo_arg


@pytest.mark.asyncio
async def test_upsert_entity_none_geometry():
    from datetime import datetime

    conn = AsyncMock()
    entity = CanonicalEntity(
        id="e2",
        type="t",
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
        id="e3",
        type="t",
        timestamp=datetime.now(tz=timezone.utc),
        status="in_transit",
    )
    await _insert_state(conn, entity)
    conn.execute.assert_awaited_once()
    sql, entity_id, status, *_ = conn.execute.call_args.args
    assert "INSERT INTO entity_state" in sql
    assert entity_id == "e3"
    assert status == "in_transit"


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
    assert enum_values == {"opensky_flights", "shipping_demo"}


@pytest.mark.asyncio
async def test_call_ingestion_tool_success():
    pool, _ = _make_pool_mock()

    with patch(
        "src.ingestion.tool.run_ingestion",
        new_callable=AsyncMock,
        return_value=22,
    ):
        result = await call_ingestion_tool(
            {"adapter_id": "shipping_demo"},
            pool,
            settings=_BOTH_DOMAINS,
        )

    assert result["success"] is True
    assert result["adapter_id"] == "shipping_demo"
    assert result["entities_upserted"] == 22


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
        {"adapter_id": "shipping_demo"},
        pool,
        settings=aviation_only,
    )
    assert result["success"] is False
    assert "shipping_demo" in result["error"]


# ── IngestionAdapter Protocol conformance ─────────────────────────────────────


def test_shipping_adapter_satisfies_protocol():
    adapter = ShippingDemoAdapter()
    assert isinstance(adapter, IngestionAdapter)
    assert adapter.adapter_id == "shipping_demo"
