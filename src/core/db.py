"""Database client factories.

Two separate stores:
  - Postgres (asyncpg pool)  — PostGIS live store + pgvector RAG embeddings
  - Neo4j   (async driver)   — Property graph (entities, dependencies, events)
"""
from __future__ import annotations

import asyncpg
from neo4j import AsyncDriver, AsyncGraphDatabase

from src.core.config import Settings


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
