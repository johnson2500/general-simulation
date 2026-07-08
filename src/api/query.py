"""POST /query — ReAct agent reasoning pipeline endpoint."""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends
from neo4j import AsyncDriver

from src.api.deps import get_llm_client, get_neo4j_driver, get_pool, get_solver
from src.core.solver import Solver
from src.llm.base import LLMClientBase
from src.reasoning.pipeline import run_pipeline
from src.reasoning.types import QueryRequest, QueryResponse

router = APIRouter()


@router.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    driver: AsyncDriver = Depends(get_neo4j_driver),
    pool: asyncpg.Pool = Depends(get_pool),
    llm_client: LLMClientBase = Depends(get_llm_client),
    solver: Solver = Depends(get_solver),
) -> QueryResponse:
    """Run the ReAct agent pipeline for a simulation scenario.

    Returns a grounded LLM answer together with the affected entity set,
    solver numbers, and an ordered tool_call_trace for full auditability.
    """
    return await run_pipeline(
        request=body,
        driver=driver,
        pool=pool,
        llm_client=llm_client,
        solver=solver,
    )
