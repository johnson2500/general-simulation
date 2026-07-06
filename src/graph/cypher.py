"""Neo4j session helpers.

All direct Neo4j interaction for graph modules goes through here.
The rest of the app (api, reasoning) holds only the AsyncDriver object and
never imports neo4j symbols directly.

Exports:
  - ``neo4j_session`` — async context manager yielding a driver session
  - ``NEO4J_DATABASE`` — canonical database name used across the project
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from neo4j import AsyncDriver, AsyncSession

NEO4J_DATABASE = "neo4j"


@asynccontextmanager
async def neo4j_session(driver: AsyncDriver) -> AsyncIterator[AsyncSession]:
    """Yield a Neo4j async session scoped to the project database."""
    async with driver.session(database=NEO4J_DATABASE) as session:
        yield session
