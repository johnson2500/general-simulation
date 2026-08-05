"""Unit tests for spatial overlay helpers (no live DB)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.graph.spatial_overlay import (
    UK_AIRSPACE_BBOX,
    format_bbox,
    parse_bbox,
)


def test_parse_bbox_string() -> None:
    assert parse_bbox("-12,49,3,59") == (-12.0, 49.0, 3.0, 59.0)


def test_parse_bbox_tuple() -> None:
    assert parse_bbox(UK_AIRSPACE_BBOX) == UK_AIRSPACE_BBOX


def test_parse_bbox_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        parse_bbox("1,2,3")


def test_format_bbox_roundtrip() -> None:
    assert parse_bbox(format_bbox(UK_AIRSPACE_BBOX)) == UK_AIRSPACE_BBOX


@pytest.mark.asyncio
async def test_list_entities_in_bbox_queries_postgis() -> None:
    from src.graph.spatial_overlay import list_entities_in_bbox

    pool = AsyncMock()
    pool.fetch = AsyncMock(
        return_value=[
            {"id": "opensky-aaa", "type": "moving_entity"},
            {"id": "opensky-bbb", "type": "moving_entity"},
        ]
    )

    rows = await list_entities_in_bbox(pool, "-12,49,3,59", limit=100)

    assert [r["id"] for r in rows] == ["opensky-aaa", "opensky-bbb"]
    pool.fetch.assert_awaited_once()
    sql = pool.fetch.await_args.args[0]
    assert "ST_MakeEnvelope" in sql
    assert "geometry" in sql


@pytest.mark.asyncio
async def test_sync_event_affected_from_bbox_merges_entities() -> None:
    from src.graph.spatial_overlay import sync_event_affected_from_bbox

    pool = AsyncMock()
    pool.fetch = AsyncMock(
        return_value=[{"id": "ac-1", "type": "moving_entity"}]
    )

    session = AsyncMock()
    session.run = AsyncMock()
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)

    ids = await sync_event_affected_from_bbox(
        driver, pool, event_id="evt-1", bbox="-12,49,3,59"
    )

    assert ids == ["ac-1"]
    assert session.run.await_count == 2  # clear edges + unwind merge
    first_cypher = session.run.await_args_list[0].args[0]
    assert "affect_bbox" in first_cypher
    assert "DELETE r" in first_cypher
