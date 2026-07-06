"""POST /query — three-stage reasoning pipeline endpoint."""
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
    """Run the three-stage reasoning pipeline for a simulation scenario.

    Returns a grounded answer (Stage-3 LLM synthesis) together with the
    auditable Stage-1 affected entity set and Stage-2 solver numbers.
    """
    return await run_pipeline(
        request=body,
        driver=driver,
        pool=pool,
        llm_client=llm_client,
        solver=solver,
    )
