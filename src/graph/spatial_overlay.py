"""Spatial overlay helpers — wire SimulationEvent AFFECTED_BY edges from PostGIS.

Live entity positions live in Postgres.  Simulation overlays live in Neo4j.
When an event declares ``affect_bbox``, the affected set is refreshed from
whatever entities currently sit inside that bbox so queries track the live
store instead of a static mock ID list.
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg
from neo4j import AsyncDriver

from src.graph.cypher import neo4j_session
from src.graph.events import EDGE_AFFECTED_BY

logger = logging.getLogger(__name__)

# Approximate London / Scottish FIR envelope used by the UK airspace demo.
UK_AIRSPACE_BBOX = (-12.0, 49.0, 3.0, 59.0)


def parse_bbox(bbox: str | tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Parse ``minLon,minLat,maxLon,maxLat`` (or a 4-tuple) into floats."""
    if isinstance(bbox, tuple):
        if len(bbox) != 4:
            raise ValueError("bbox tuple must have 4 values")
        return bbox
    parts = [p.strip() for p in bbox.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be minLon,minLat,maxLon,maxLat")
    min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    return min_lon, min_lat, max_lon, max_lat


def format_bbox(bbox: tuple[float, float, float, float]) -> str:
    return ",".join(str(v) for v in bbox)


async def list_entities_in_bbox(
    pool: asyncpg.Pool,
    bbox: str | tuple[float, float, float, float],
    *,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Return live-store entities whose geometry intersects *bbox*."""
    min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
    rows = await pool.fetch(
        """
        SELECT id, type
        FROM entity
        WHERE geometry IS NOT NULL
          AND geometry && ST_MakeEnvelope($1, $2, $3, $4, 4326)
        ORDER BY updated_at DESC
        LIMIT $5
        """,
        min_lon,
        min_lat,
        max_lon,
        max_lat,
        limit,
    )
    return [{"id": r["id"], "type": r["type"]} for r in rows]


async def sync_event_affected_from_bbox(
    driver: AsyncDriver,
    pool: asyncpg.Pool,
    *,
    event_id: str,
    bbox: str | tuple[float, float, float, float],
    limit: int = 2000,
) -> list[str]:
    """Replace AFFECTED_BY edges for *event_id* with entities currently in *bbox*.

    - MERGEs Entity nodes so Neo4j stays aligned with PostGIS IDs.
    - Does not mutate Postgres live data.
    - Stores ``affect_bbox`` on the SimulationEvent for later refreshes.
    """
    bbox_str = format_bbox(parse_bbox(bbox))
    entities = await list_entities_in_bbox(pool, bbox_str, limit=limit)
    entity_ids = [e["id"] for e in entities]

    async with neo4j_session(driver) as session:
        # Ensure the event exists and record the spatial scope.
        await session.run(
            "MERGE (e:SimulationEvent {id: $id}) "
            "SET e.affect_bbox = $bbox "
            "WITH e "
            "OPTIONAL MATCH (n)-[r:AFFECTED_BY]->(e) "
            "DELETE r",
            id=event_id,
            bbox=bbox_str,
        )

        if entities:
            await session.run(
                "UNWIND $rows AS row "
                "MERGE (n:Entity {id: row.id}) "
                "SET n.type = row.type "
                "WITH n "
                "MATCH (e:SimulationEvent {id: $eid}) "
                f"MERGE (n)-[:{EDGE_AFFECTED_BY}]->(e)",
                rows=entities,
                eid=event_id,
            )

    logger.info(
        "Synced spatial overlay: event=%s bbox=%s affected=%d",
        event_id,
        bbox_str,
        len(entity_ids),
    )
    return entity_ids


async def refresh_scenario_spatial_overlays(
    driver: AsyncDriver,
    pool: asyncpg.Pool,
    scenario_id: str,
) -> dict[str, int]:
    """Refresh every event in *scenario_id* that has an ``affect_bbox`` property."""
    async with neo4j_session(driver) as session:
        result = await session.run(
            "MATCH (e:SimulationEvent {scenario_id: $sid}) "
            "WHERE e.affect_bbox IS NOT NULL "
            "RETURN e.id AS id, e.affect_bbox AS bbox",
            sid=scenario_id,
        )
        events = await result.data()

    totals: dict[str, int] = {}
    for ev in events:
        eid = ev.get("id")
        bbox = ev.get("bbox")
        if not eid or not bbox:
            continue
        ids = await sync_event_affected_from_bbox(
            driver, pool, event_id=eid, bbox=str(bbox)
        )
        totals[str(eid)] = len(ids)

    if totals:
        logger.info(
            "Refreshed spatial overlays for scenario=%s events=%s",
            scenario_id,
            totals,
        )
    return totals
