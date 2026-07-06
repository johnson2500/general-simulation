"""Idempotent schema bootstrap.

Creates (or no-ops if already present):

  Postgres:
    - Extensions: vector, postgis
    - Live-store tables: entity, entity_state  (PostGIS + JSONB, domain-agnostic)

  Neo4j:
    - Uniqueness constraints on Entity.id and SimulationEvent.id
    - Lookup indexes on Entity.type and SimulationEvent.scenario_id

Apache AGE is no longer used — the property graph lives in Neo4j.

Run directly:
    uv run python -m src.graph.bootstrap

Or import and call from application / test code:
    await bootstrap_postgres(dsn="postgresql://...")
    await bootstrap_neo4j(driver)

Safe to re-run on an already-bootstrapped database / graph.
"""
from __future__ import annotations

import asyncio
import logging
import sys

import asyncpg
from neo4j import AsyncDriver

from src.core.config import Settings
from src.core.db import create_neo4j_driver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Postgres DDL — extensions and live-store tables only (no AGE)
# ---------------------------------------------------------------------------

_EXTENSION_STATEMENTS: list[str] = [
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS postgis",
]

_TABLE_STATEMENTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS entity (
        id          TEXT        PRIMARY KEY,
        type        TEXT        NOT NULL,
        geometry    geometry(Geometry, 4326),
        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        attributes  JSONB       NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entity_state (
        id          BIGSERIAL   PRIMARY KEY,
        entity_id   TEXT        NOT NULL
                        REFERENCES entity(id) ON DELETE CASCADE,
        status      TEXT        NOT NULL,
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        attributes  JSONB       NOT NULL DEFAULT '{}'::jsonb
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_entity_type         ON entity (type)",
    "CREATE INDEX IF NOT EXISTS idx_entity_geometry     ON entity USING GIST (geometry) WHERE geometry IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_entity_state_entity ON entity_state (entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_entity_state_time   ON entity_state (recorded_at DESC)",
]

# ---------------------------------------------------------------------------
# Neo4j DDL — constraints and indexes
# ---------------------------------------------------------------------------

_NEO4J_CONSTRAINTS: list[str] = [
    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT sim_event_id IF NOT EXISTS FOR (n:SimulationEvent) REQUIRE n.id IS UNIQUE",
]

_NEO4J_INDEXES: list[str] = [
    "CREATE INDEX entity_type IF NOT EXISTS FOR (n:Entity) ON (n.type)",
    "CREATE INDEX sim_event_scenario IF NOT EXISTS FOR (n:SimulationEvent) ON (n.scenario_id)",
]

# ---------------------------------------------------------------------------
# Bootstrap functions
# ---------------------------------------------------------------------------


async def bootstrap_postgres(dsn: str) -> None:
    """Run Postgres DDL against *dsn*.  Safe to call repeatedly."""
    conn: asyncpg.Connection = await asyncpg.connect(dsn)
    try:
        logger.info("Bootstrapping Postgres …")

        for stmt in _EXTENSION_STATEMENTS:
            logger.debug("exec: %s", stmt.strip())
            await conn.execute(stmt)
        logger.info("Extensions ready (vector, postgis)")

        async with conn.transaction():
            for stmt in _TABLE_STATEMENTS:
                logger.debug("exec: %s", stmt.strip()[:80])
                await conn.execute(stmt)
        logger.info("Live-store tables and indexes ready")

        logger.info("Postgres bootstrap complete.")
    finally:
        await conn.close()


async def bootstrap_neo4j(driver: AsyncDriver) -> None:
    """Create Neo4j constraints and indexes.  Safe to call repeatedly."""
    logger.info("Bootstrapping Neo4j …")
    async with driver.session(database="neo4j") as session:
        for stmt in _NEO4J_CONSTRAINTS:
            logger.debug("neo4j: %s", stmt)
            await session.run(stmt)
        for stmt in _NEO4J_INDEXES:
            logger.debug("neo4j: %s", stmt)
            await session.run(stmt)
    logger.info("Neo4j constraints and indexes ready.")


async def bootstrap(dsn: str, neo4j_driver: AsyncDriver) -> None:
    """Run full bootstrap (Postgres + Neo4j)."""
    await bootstrap_postgres(dsn)
    await bootstrap_neo4j(neo4j_driver)


async def _run() -> None:
    settings = Settings()
    driver = create_neo4j_driver(settings)
    try:
        await bootstrap(settings.postgres_dsn, driver)
    finally:
        await driver.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s — %(message)s",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())  # type: ignore[arg-type]
