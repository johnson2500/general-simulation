"""Database client factories.

Two separate stores:
  - Postgres (asyncpg pool)  — PostGIS live store + pgvector RAG embeddings
  - Neo4j   (async driver)   — Property graph (entities, dependencies, events)
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg
from neo4j import AsyncDriver, AsyncGraphDatabase

from src.core.config import Settings

logger = logging.getLogger(__name__)


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Create and return an asyncpg connection pool for Postgres.

    Used by the live store (PostGIS entity/entity_state tables) and the
    pgvector embeddings layer.  No AGE initialisation needed.
    """
    return await asyncpg.create_pool(
        settings.postgres_dsn,
        min_size=1,
        max_size=10,
    )


def create_neo4j_driver(settings: Settings) -> AsyncDriver:
    """Create and return a Neo4j async driver.

    The driver is long-lived (one per process).  Callers open sessions as
    needed via ``async with driver.session(...)``.  Close with
    ``await driver.close()`` on shutdown.
    """
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )


async def wait_for_pool(
    settings: Settings,
    *,
    timeout_seconds: float | None = None,
    interval_seconds: float | None = None,
) -> asyncpg.Pool:
    """Retry ``create_pool`` until Postgres accepts connections or timeout."""
    timeout = (
        settings.startup_db_wait_seconds
        if timeout_seconds is None
        else timeout_seconds
    )
    interval = (
        settings.startup_db_wait_interval_seconds
        if interval_seconds is None
        else interval_seconds
    )
    deadline = asyncio.get_running_loop().time() + timeout
    attempt = 0
    last_exc: Exception | None = None
    while True:
        attempt += 1
        try:
            pool = await create_pool(settings)
            logger.info("Postgres pool ready (attempt %s)", attempt)
            return pool
        except Exception as exc:
            last_exc = exc
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            logger.warning(
                "Postgres not ready (attempt %s, %.0fs left): %s",
                attempt,
                remaining,
                exc,
            )
            await asyncio.sleep(min(interval, remaining))
    raise RuntimeError(
        f"Postgres unavailable after {timeout:.0f}s ({attempt} attempts)"
    ) from last_exc


async def wait_for_neo4j(
    settings: Settings,
    *,
    timeout_seconds: float | None = None,
    interval_seconds: float | None = None,
) -> AsyncDriver:
    """Create a Neo4j driver and retry until connectivity is verified."""
    timeout = (
        settings.startup_db_wait_seconds
        if timeout_seconds is None
        else timeout_seconds
    )
    interval = (
        settings.startup_db_wait_interval_seconds
        if interval_seconds is None
        else interval_seconds
    )
    driver = create_neo4j_driver(settings)
    deadline = asyncio.get_running_loop().time() + timeout
    attempt = 0
    last_exc: Exception | None = None
    while True:
        attempt += 1
        try:
            await driver.verify_connectivity()
            logger.info("Neo4j driver ready (attempt %s)", attempt)
            return driver
        except Exception as exc:
            last_exc = exc
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            logger.warning(
                "Neo4j not ready (attempt %s, %.0fs left): %s",
                attempt,
                remaining,
                exc,
            )
            await asyncio.sleep(min(interval, remaining))
    await driver.close()
    raise RuntimeError(
        f"Neo4j unavailable after {timeout:.0f}s ({attempt} attempts)"
    ) from last_exc
