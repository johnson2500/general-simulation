"""Ingestion runner.

Calls an IngestionAdapter, normalises the result, and upserts canonical
entities into the PostGIS live store (entity + entity_state tables) and
optionally into the Neo4j property graph (Entity nodes for graph traversal).

This module writes *ground-truth data only*.  Simulations are overlays
applied at query time and must never be written here.

Callable two ways (BUILD_PLAN task 4):
  1. CLI / one-shot  — ``python -m src.ingestion`` (OpenShift CronJob)
  2. Programmatic    — ``await run_ingestion(adapter, pool)``
     (the reasoning layer calls this via the Llama Stack tool wrapper)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import asyncpg
from neo4j import AsyncDriver

from src.core.ingestion import CanonicalEntity, IngestionAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL helpers (kept here so the tool wrapper doesn't duplicate them)
# ---------------------------------------------------------------------------

_UPSERT_ENTITY = """
INSERT INTO entity (id, type, geometry, created_at, updated_at, attributes)
VALUES (
    $1,
    $2,
    CASE
        WHEN $3::text IS NOT NULL
        THEN ST_SetSRID(ST_GeomFromGeoJSON($3::text), 4326)
        ELSE NULL
    END,
    NOW(),
    NOW(),
    $4::jsonb
)
ON CONFLICT (id) DO UPDATE SET
    type       = EXCLUDED.type,
    geometry   = EXCLUDED.geometry,
    updated_at = NOW(),
    attributes = EXCLUDED.attributes
"""

_INSERT_STATE = """
INSERT INTO entity_state (entity_id, status, recorded_at, attributes)
VALUES ($1, $2, $3, $4::jsonb)
"""


# ---------------------------------------------------------------------------
# Public callable
# ---------------------------------------------------------------------------


async def run_ingestion(
    adapter: IngestionAdapter,
    pool: asyncpg.Pool,
    neo4j_driver: AsyncDriver | None = None,
) -> int:
    """Execute one ingestion cycle for *adapter*.

    Writes to two stores:
      - Postgres (always): entity + entity_state rows in a single transaction.
      - Neo4j (when *neo4j_driver* is supplied): Entity nodes are bulk-merged
        so the graph layer can traverse and simulate against live entities.

    Returns the number of entities upserted.
    """
    logger.info("Starting ingestion: adapter=%s", adapter.adapter_id)

    raw = await adapter.fetch()
    entities: list[CanonicalEntity] = adapter.normalize(raw)

    if not entities:
        logger.info("adapter=%s returned 0 entities — nothing to upsert", adapter.adapter_id)
        return 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            for entity in entities:
                await _upsert_entity(conn, entity)
                await _insert_state(conn, entity)

    if neo4j_driver is not None:
        await _upsert_entities_neo4j(neo4j_driver, entities)

    logger.info(
        "Ingestion complete: adapter=%s upserted=%d",
        adapter.adapter_id,
        len(entities),
    )
    return len(entities)


# ---------------------------------------------------------------------------
# Internal helpers (also used by tests via direct import)
# ---------------------------------------------------------------------------


async def _upsert_entity(
    conn: asyncpg.Connection,
    entity: CanonicalEntity,
) -> None:
    geo_json: str | None = (
        json.dumps(entity.geometry) if entity.geometry else None
    )
    attrs = json.dumps(entity.attributes)
    await conn.execute(_UPSERT_ENTITY, entity.id, entity.type, geo_json, attrs)


async def _insert_state(
    conn: asyncpg.Connection,
    entity: CanonicalEntity,
) -> None:
    ts = entity.timestamp
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    await conn.execute(
        _INSERT_STATE,
        entity.id,
        entity.status,
        ts,
        "{}",  # state-specific attributes; domain adapters can extend this
    )


async def _upsert_entities_neo4j(
    driver: AsyncDriver,
    entities: list[CanonicalEntity],
) -> None:
    """Bulk-merge Entity nodes into Neo4j using a single UNWIND query.

    Uses MERGE so re-running ingestion is idempotent.  Only id and type are
    written here — dependency edges and simulation overlays are managed
    separately via src.graph.nodes and src.graph.events.
    """
    rows = [{"id": e.id, "type": e.type} for e in entities]
    query = (
        "UNWIND $rows AS row "
        "MERGE (n:Entity {id: row.id}) "
        "SET n.type = row.type"
    )
    try:
        async with driver.session(database="neo4j") as session:
            await session.run(query, rows=rows)
        logger.debug("Neo4j entity nodes merged: count=%d", len(rows))
    except Exception:
        logger.exception(
            "Neo4j entity upsert failed — Postgres write already committed; "
            "Neo4j will be out of sync until next ingestion cycle"
        )
