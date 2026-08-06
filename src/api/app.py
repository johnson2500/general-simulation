"""FastAPI application factory with lifespan resource management."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.health import router as health_router
from src.api.query import router as query_router
from src.api.admin import router as admin_router

logger = logging.getLogger(__name__)

# Local Vite dev origins (apps/simulation-console). Same-origin in production
# if the SPA is served from the API or an OpenShift Route.
_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise shared resources on startup; clean up on shutdown.

    Blocks until Postgres and Neo4j accept connections so the process never
    serves traffic with ``app.state.pool is None`` (common race on cold
    OpenShift installs when the API pod starts before its databases).
    """
    from src.core.config import Settings
    from src.core.db import wait_for_neo4j, wait_for_pool
    from src.ingestion.registry import resolve_solver
    from src.llm.factory import get_llm_client

    settings = Settings()

    pool = await wait_for_pool(settings)
    neo4j_driver = await wait_for_neo4j(settings)

    app.state.pool = pool
    app.state.neo4j_driver = neo4j_driver
    app.state.llm_client = get_llm_client(settings, pool)
    app.state.solver = resolve_solver(settings)

    yield

    await pool.close()
    logger.info("Postgres pool closed")
    await neo4j_driver.close()
    logger.info("Neo4j driver closed")


app = FastAPI(
    title="General Simulation & Impact-Reasoning Platform",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(query_router)
app.include_router(admin_router)
