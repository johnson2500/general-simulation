"""FastAPI application factory with lifespan resource management."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from src.api.health import router as health_router
from src.api.query import router as query_router
from src.api.admin import router as admin_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise shared resources on startup; clean up on shutdown."""
    from src.core.config import Settings
    from src.core.db import create_pool, create_neo4j_driver
    from src.llm.factory import get_llm_client
    from src.solver.stub import StubSolver

    settings = Settings()

    pool = None
    neo4j_driver = None
    try:
        pool = await create_pool(settings)
        logger.info("Postgres pool ready")
    except Exception as exc:
        logger.warning("Could not create Postgres pool at startup: %s", exc)

    try:
        neo4j_driver = create_neo4j_driver(settings)
        logger.info("Neo4j driver ready")
    except Exception as exc:
        logger.warning("Could not create Neo4j driver at startup: %s", exc)

    app.state.pool = pool
    app.state.neo4j_driver = neo4j_driver
    app.state.llm_client = get_llm_client(settings, pool)
    app.state.solver = StubSolver()

    yield

    if pool is not None:
        await pool.close()
        logger.info("Postgres pool closed")
    if neo4j_driver is not None:
        await neo4j_driver.close()
        logger.info("Neo4j driver closed")


app = FastAPI(
    title="General Simulation & Impact-Reasoning Platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(query_router)
app.include_router(admin_router)
