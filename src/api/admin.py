"""Admin interface — browse Postgres entities and Neo4j graph data.

All routes are mounted under /admin.  The HTML SPA is served at GET /admin/
and makes same-origin API calls to the endpoints below.

Endpoint summary
----------------
GET  /admin/                       Serve the admin SPA (HTML)
GET  /admin/stats                  Overview counts (Postgres + graph)
GET  /admin/entity-types           Distinct entity types in Postgres
GET  /admin/entities               Paginated entity list (Postgres)
GET  /admin/entities/geojson       GeoJSON FeatureCollection (entities with geometry)
GET  /admin/entities/{id}          Entity detail + state history
GET  /admin/graph/nodes            Entity nodes from Neo4j graph
GET  /admin/graph/scenarios        Distinct scenario IDs from Neo4j
GET  /admin/graph/events           SimulationEvent nodes (optional filter)
GET  /admin/graph/edges            Dependency / AFFECTED_BY edges
POST /admin/graph/events           Inject a new simulation event
POST /admin/graph/scenarios/{id}/sync-spatial  Refresh AFFECTED_BY from PostGIS bbox
DELETE /admin/graph/scenarios/{id} Remove scenario from graph + vector DB
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from neo4j import AsyncDriver
from pydantic import BaseModel

from src.api.deps import get_llm_client, get_neo4j_driver, get_pool
from src.graph.cypher import neo4j_session
from src.graph.events import SimulationEvent, inject_event, remove_scenario
from src.llm.base import LLMClientBase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

_STATIC = Path(__file__).parent / "static"


# ── UI ────────────────────────────────────────────────────────────────────────

@router.get("/", include_in_schema=False)
async def admin_ui() -> FileResponse:
    """Serve the admin SPA."""
    return FileResponse(_STATIC / "admin.html")


# ── Overview stats ────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    pool: asyncpg.Pool = Depends(get_pool),
    driver: AsyncDriver = Depends(get_neo4j_driver),
) -> dict[str, int]:
    """Return aggregate counts from Postgres and the Neo4j graph."""
    entity_count: int = await pool.fetchval("SELECT COUNT(*) FROM entity") or 0
    state_count: int = await pool.fetchval("SELECT COUNT(*) FROM entity_state") or 0

    async with neo4j_session(driver) as session:
        node_result = await session.run("MATCH (n:Entity) RETURN count(n) AS cnt")
        node_record = await node_result.single()
        graph_nodes: int = node_record["cnt"] if node_record else 0

        event_result = await session.run("MATCH (e:SimulationEvent) RETURN count(e) AS cnt")
        event_record = await event_result.single()
        graph_events: int = event_record["cnt"] if event_record else 0

        scenario_result = await session.run(
            "MATCH (e:SimulationEvent) RETURN DISTINCT e.scenario_id AS sid"
        )
        scenario_records = await scenario_result.data()
        scenario_count = len([r for r in scenario_records if r.get("sid")])

    return {
        "entity_count": entity_count,
        "state_count": state_count,
        "graph_nodes": graph_nodes,
        "graph_events": graph_events,
        "scenario_count": scenario_count,
    }


# ── Postgres: entities ────────────────────────────────────────────────────────

@router.get("/entity-types")
async def list_entity_types(pool: asyncpg.Pool = Depends(get_pool)) -> list[str]:
    """Return distinct entity types present in the live store."""
    rows = await pool.fetch("SELECT DISTINCT type FROM entity ORDER BY type")
    return [r["type"] for r in rows]


@router.get("/entities")
async def list_entities(
    type: str | None = Query(None),
    search: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    """Return a paginated list of entities from the live Postgres store."""
    conditions: list[str] = []
    args: list[Any] = []

    if type:
        args.append(type)
        conditions.append(f"type = ${len(args)}")
    if search:
        args.append(f"%{search}%")
        conditions.append(f"id ILIKE ${len(args)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    total: int = await pool.fetchval(
        f"SELECT COUNT(*) FROM entity {where}", *args
    ) or 0

    page_args = args + [limit, offset]
    rows = await pool.fetch(
        f"SELECT id, type, attributes, created_at, updated_at "
        f"FROM entity {where} ORDER BY updated_at DESC "
        f"LIMIT ${len(page_args) - 1} OFFSET ${len(page_args)}",
        *page_args,
    )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r["id"],
                "type": r["type"],
                "attributes": json.loads(r["attributes"]) if r["attributes"] else {},
                "created_at": r["created_at"].isoformat(),
                "updated_at": r["updated_at"].isoformat(),
            }
            for r in rows
        ],
    }


@router.get("/entities/geojson")
async def list_entities_geojson(
    type: str | None = Query(None, description="Filter by entity type"),
    bbox: str | None = Query(
        None,
        description="Optional bbox: minLon,minLat,maxLon,maxLat (WGS84)",
    ),
    ids: str | None = Query(
        None,
        description="Optional comma-separated entity IDs to include (bypasses updated_at ranking)",
    ),
    limit: int = Query(2000, ge=1, le=10000),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection of entities that have geometry.

    Entities without geometry are omitted.  Optional *type*, *bbox*, and *ids*
    filters narrow the result for map views.
    """
    conditions: list[str] = ["e.geometry IS NOT NULL"]
    args: list[Any] = []

    if type:
        args.append(type)
        conditions.append(f"e.type = ${len(args)}")

    if ids:
        id_list = [part.strip() for part in ids.split(",") if part.strip()]
        if id_list:
            args.append(id_list)
            conditions.append(f"e.id = ANY(${len(args)}::text[])")

    if bbox:
        parts = [p.strip() for p in bbox.split(",")]
        if len(parts) != 4:
            raise HTTPException(
                status_code=422,
                detail="bbox must be minLon,minLat,maxLon,maxLat",
            )
        try:
            min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail="bbox values must be numeric",
            ) from exc
        args.extend([min_lon, min_lat, max_lon, max_lat])
        conditions.append(
            f"e.geometry && ST_MakeEnvelope("
            f"${len(args) - 3}, ${len(args) - 2}, ${len(args) - 1}, ${len(args)}, 4326)"
        )

    args.append(limit)
    where = " AND ".join(conditions)
    rows = await pool.fetch(
        f"""
        SELECT
            e.id,
            e.type,
            e.attributes,
            e.updated_at,
            ST_AsGeoJSON(e.geometry) AS geojson,
            s.status AS latest_status
        FROM entity e
        LEFT JOIN LATERAL (
            SELECT status
            FROM entity_state
            WHERE entity_id = e.id
            ORDER BY recorded_at DESC
            LIMIT 1
        ) s ON TRUE
        WHERE {where}
        ORDER BY e.updated_at DESC
        LIMIT ${len(args)}
        """,
        *args,
    )

    features: list[dict[str, Any]] = []
    for r in rows:
        if not r["geojson"]:
            continue
        attrs = json.loads(r["attributes"]) if r["attributes"] else {}
        features.append(
            {
                "type": "Feature",
                "id": r["id"],
                "geometry": json.loads(r["geojson"]),
                "properties": {
                    "id": r["id"],
                    "type": r["type"],
                    "status": r["latest_status"],
                    "attributes": attrs,
                    "updated_at": r["updated_at"].isoformat(),
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}


@router.get("/entities/{entity_id}")
async def get_entity(
    entity_id: str,
    states_limit: int = Query(20, ge=1, le=200),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    """Return entity details plus its most-recent state history."""
    row = await pool.fetchrow(
        "SELECT id, type, attributes, created_at, updated_at "
        "FROM entity WHERE id = $1",
        entity_id,
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Entity '{entity_id}' not found")

    states = await pool.fetch(
        "SELECT status, recorded_at, attributes FROM entity_state "
        "WHERE entity_id = $1 ORDER BY recorded_at DESC LIMIT $2",
        entity_id,
        states_limit,
    )

    return {
        "id": row["id"],
        "type": row["type"],
        "attributes": json.loads(row["attributes"]) if row["attributes"] else {},
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "states": [
            {
                "status": s["status"],
                "recorded_at": s["recorded_at"].isoformat(),
                "attributes": json.loads(s["attributes"]) if s["attributes"] else {},
            }
            for s in states
        ],
    }


# ── Graph: nodes ──────────────────────────────────────────────────────────────

@router.get("/graph/nodes")
async def list_graph_nodes(
    limit: int = Query(100, ge=1, le=1000),
    driver: AsyncDriver = Depends(get_neo4j_driver),
) -> list[dict[str, Any]]:
    """Return Entity nodes from the Neo4j graph with all properties."""
    async with neo4j_session(driver) as session:
        result = await session.run(
            "MATCH (n:Entity) RETURN properties(n) AS props LIMIT $limit",
            limit=limit,
        )
        records = await result.data()
    return [r["props"] for r in records if isinstance(r.get("props"), dict)]


# ── Graph: scenarios & events ─────────────────────────────────────────────────

@router.get("/graph/scenarios")
async def list_scenarios(
    driver: AsyncDriver = Depends(get_neo4j_driver),
) -> list[str]:
    """Return all distinct scenario IDs present in the Neo4j graph."""
    async with neo4j_session(driver) as session:
        result = await session.run(
            "MATCH (e:SimulationEvent) RETURN DISTINCT e.scenario_id AS sid"
        )
        records = await result.data()
    return [r["sid"] for r in records if r.get("sid") is not None]


@router.get("/graph/events")
async def list_graph_events(
    scenario_id: str | None = Query(None),
    driver: AsyncDriver = Depends(get_neo4j_driver),
) -> list[dict[str, Any]]:
    """Return SimulationEvent nodes, optionally filtered by scenario_id."""
    async with neo4j_session(driver) as session:
        if scenario_id:
            result = await session.run(
                "MATCH (e:SimulationEvent {scenario_id: $sid}) "
                "RETURN properties(e) AS props",
                sid=scenario_id,
            )
        else:
            result = await session.run(
                "MATCH (e:SimulationEvent) RETURN properties(e) AS props LIMIT 200"
            )
        records = await result.data()
    return [r["props"] for r in records if isinstance(r.get("props"), dict)]


# ── Graph: edges ──────────────────────────────────────────────────────────────

@router.get("/graph/edges")
async def list_graph_edges(
    limit: int = Query(200, ge=1, le=2000),
    driver: AsyncDriver = Depends(get_neo4j_driver),
) -> list[dict[str, Any]]:
    """Return all edges (dependency + AFFECTED_BY) between graph nodes."""
    async with neo4j_session(driver) as session:
        result = await session.run(
            "MATCH (a)-[r]->(b) "
            "RETURN a.id AS from_id, type(r) AS edge_type, b.id AS to_id "
            "LIMIT $limit",
            limit=limit,
        )
        records = await result.data()
    return [
        {"from_id": r["from_id"], "edge_type": r["edge_type"], "to_id": r["to_id"]}
        for r in records
        if r.get("from_id") and r.get("edge_type") and r.get("to_id")
    ]


# ── Graph: inject / remove ────────────────────────────────────────────────────

class InjectEventRequest(BaseModel):
    id: str
    scenario_id: str
    description: str
    affected_entity_ids: list[str] = []
    bbox: str | None = None  # minLon,minLat,maxLon,maxLat — resolves from PostGIS
    attributes: dict[str, Any] = {}


@router.post("/graph/events", status_code=201)
async def inject_graph_event(
    body: InjectEventRequest,
    driver: AsyncDriver = Depends(get_neo4j_driver),
    pool: asyncpg.Pool = Depends(get_pool),
    llm_client: LLMClientBase = Depends(get_llm_client),
) -> dict[str, Any]:
    """Inject a simulation event overlay (additive — does not modify live data).

    Provide ``affected_entity_ids`` and/or ``bbox``.  When ``bbox`` is set,
    AFFECTED_BY edges are resolved from live PostGIS entities in that envelope
    and re-synced on every subsequent ``/query`` for the scenario.
    """
    from src.graph.spatial_overlay import (
        format_bbox,
        parse_bbox,
        sync_event_affected_from_bbox,
    )

    if not body.affected_entity_ids and not body.bbox:
        raise HTTPException(
            status_code=400,
            detail="Provide affected_entity_ids and/or bbox",
        )

    affect_bbox: str | None = None
    affected_ids = list(body.affected_entity_ids)

    if body.bbox:
        try:
            affect_bbox = format_bbox(parse_bbox(body.bbox))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    event = SimulationEvent(
        id=body.id,
        scenario_id=body.scenario_id,
        description=body.description,
        affected_entity_ids=affected_ids,
        affect_bbox=affect_bbox,
        attributes=body.attributes,
    )
    await inject_event(event, driver, llm_client)

    if affect_bbox:
        affected_ids = await sync_event_affected_from_bbox(
            driver, pool, event_id=body.id, bbox=affect_bbox
        )

    return {
        "status": "injected",
        "event_id": body.id,
        "affected_count": len(affected_ids),
        "affect_bbox": affect_bbox,
    }


@router.post("/graph/scenarios/{scenario_id}/sync-spatial")
async def sync_scenario_spatial(
    scenario_id: str,
    driver: AsyncDriver = Depends(get_neo4j_driver),
    pool: asyncpg.Pool = Depends(get_pool),
) -> dict[str, Any]:
    """Refresh AFFECTED_BY edges from PostGIS for events with ``affect_bbox``."""
    from src.graph.spatial_overlay import refresh_scenario_spatial_overlays

    totals = await refresh_scenario_spatial_overlays(driver, pool, scenario_id)
    return {
        "status": "synced",
        "scenario_id": scenario_id,
        "events": totals,
        "total_affected": sum(totals.values()),
    }


@router.delete("/graph/scenarios/{scenario_id}", status_code=200)
async def delete_scenario(
    scenario_id: str,
    driver: AsyncDriver = Depends(get_neo4j_driver),
    llm_client: LLMClientBase = Depends(get_llm_client),
) -> dict[str, str]:
    """Remove all events for a scenario from both the graph and vector store."""
    await remove_scenario(scenario_id, driver, llm_client)
    return {"status": "removed", "scenario_id": scenario_id}
