"""Three-stage reasoning pipeline orchestrator.

Architecture (important boundary):
  We use our OWN thin orchestrator for the three stages rather than handing
  control to Llama Stack's agent loop.  Stages 1 and 2 are deterministic and
  contain NO LLM calls; Llama Stack's agent loop is built around LLM-driven
  tool calling and would fight that design.

  Stage 1 calls Neo4j directly (graph traversal).
  Stage 2 calls PostGIS directly and then calls the Solver.
  Stage 3 calls LLMClientBase for vector_search() + generate().
  The LLM client is the inference/vector *backend*, not the top-level
  controller.

  The live store is NEVER mutated by this pipeline.
"""
from __future__ import annotations

import logging

import asyncpg
from neo4j import AsyncDriver

from src.core.solver import Solver
from src.llm.base import LLMClientBase
from src.reasoning.stage1 import run_stage1
from src.reasoning.stage2 import run_stage2
from src.reasoning.stage3 import run_stage3
from src.reasoning.types import QueryRequest, QueryResponse, ResponseOptionOut, SolverResultOut
from src.solver.stub import StubSolver

logger = logging.getLogger(__name__)


async def run_pipeline(
    request: QueryRequest,
    driver: AsyncDriver,
    pool: asyncpg.Pool,
    llm_client: LLMClientBase,
    solver: Solver | None = None,
) -> QueryResponse:
    """Execute all three stages and return a fully structured response.

    Parameters
    ----------
    request:
        Incoming query (question + scenario_id).
    driver:
        Neo4j async driver — used READ-ONLY by Stage 1 (graph traversal).
    pool:
        asyncpg connection pool — used READ-ONLY by Stage 2 (PostGIS live state).
    llm_client:
        LLMClientBase — used by Stage 3 for vector search and generate.
    solver:
        Optional Solver override.  Defaults to StubSolver.
    """
    _solver: Solver = solver or StubSolver()

    logger.info(
        "Pipeline start: scenario=%s question=%r",
        request.scenario_id,
        request.question[:80],
    )

    # ── Stage 1 — Structural (deterministic, no LLM) ──────────────────────
    subgraph = await run_stage1(request.scenario_id, driver)

    # ── Stage 2 — Quantitative (live state read + solver) ─────────────────
    _live_state, solver_result = await run_stage2(subgraph, pool, _solver)

    # ── Stage 3 — Synthesis (vector retrieval + LLM) ──────────────────────
    answer = await run_stage3(
        question=request.question,
        subgraph=subgraph,
        solver_result=solver_result,
        llm_client=llm_client,
    )

    logger.info(
        "Pipeline complete: scenario=%s impact=%.3f",
        request.scenario_id,
        solver_result.impact_score,
    )

    return QueryResponse(
        question=request.question,
        scenario_id=request.scenario_id,
        answer=answer,
        affected_entities=subgraph.affected_entity_ids,
        solver=SolverResultOut(
            affected_count=solver_result.affected_count,
            max_chain_length=solver_result.max_chain_length,
            impact_score=solver_result.impact_score,
            response_options=[
                ResponseOptionOut(
                    rank=opt.rank,
                    label=opt.label,
                    description=opt.description,
                    estimated_impact_reduction=opt.estimated_impact_reduction,
                )
                for opt in solver_result.response_options
            ],
            explanation=solver_result.explanation,
        ),
    )
