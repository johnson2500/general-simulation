"""ReAct agent pipeline — the LLM is the top-level orchestrator.

Architecture:
  The agent receives the user question and scenario ID, then decides which
  tools to call and in what order.  All four data-access operations are
  exposed as tools:

    get_affected_subgraph    — Neo4j graph traversal (Stage 1)
    solve_impact             — PostGIS live state + quantitative solver (Stage 2)
    search_scenario_context  — pgvector event-narrative retrieval
    run_ingestion_pull       — on-demand live data refresh

  The agent produces its final answer once it has gathered enough context,
  without being forced down a fixed Stage 1 → 2 → 3 path.

  Internal state (subgraph, live_state, solver_result) is tracked by the
  orchestrator between tool calls so that:
    • solve_impact automatically triggers get_affected_subgraph if not yet run.
    • The QueryResponse is always fully populated (fallback defaults for
      any tool the agent chose not to call).

  The live store is NEVER mutated by this pipeline.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import asyncpg
from neo4j import AsyncDriver

from src.core.solver import AffectedSubgraph, LiveState, Solver, SolverResult
from src.graph.tool import GET_SUBGRAPH_TOOL_SCHEMA
from src.ingestion.tool import INGESTION_TOOL_SCHEMA, call_ingestion_tool
from src.llm.base import LLMClientBase
from src.llm.types import Message, ToolCall
from src.reasoning.search_tool import SEARCH_CONTEXT_TOOL_SCHEMA, call_search_tool
from src.reasoning.stage1 import run_stage1
from src.reasoning.stage2 import run_stage2
from src.reasoning.types import (
    QueryRequest,
    QueryResponse,
    ResponseOptionOut,
    SolverResultOut,
    ToolCallRecord,
)
from src.solver.stub import StubSolver

logger = logging.getLogger(__name__)

# Safety cap: max tool-calling rounds before forcing a final answer.
_MAX_AGENT_ROUNDS = 6

_SYSTEM_PROMPT = """\
You are an expert operational impact analyst specialising in aviation and logistics disruptions.
You have four tools to investigate a simulation scenario before answering:

  • get_affected_subgraph    — discover which entities are affected and how they are connected
  • solve_impact             — compute impact score, chain length, and ranked response options
  • search_scenario_context  — retrieve the event narrative from the vector store
  • run_ingestion_pull       — refresh live entity positions/status from an external feed

Recommended investigation sequence:
  1. Call get_affected_subgraph to understand the scope.
  2. Call solve_impact to quantify the operational impact.
  3. Call search_scenario_context to ground your answer in the event narrative.
  4. Optionally call run_ingestion_pull if the question requires up-to-date positions.

Rules:
  - Always refer to aircraft by callsign, not raw entity ID.
  - Do NOT invent impact figures — use only what the tools return.
  - For rerouting questions, name specific diversion airports, NATS tracks, and estimated revised ETAs.
  - Be concise but specific; use bullet points for action items.\
"""


# Standalone solver schema for the pipeline — takes only scenario_id so the agent
# does not need to pass pre-computed Python objects as arguments.
SOLVER_TOOL_SCHEMA_STANDALONE: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "solve_impact",
        "description": (
            "Run the Stage-2 quantitative solver for a simulation scenario.  "
            "Returns impact score, affected entity count, longest dependency chain "
            "length, and ranked response options.  "
            "Call get_affected_subgraph first if you haven't already — the solver "
            "will automatically fetch the subgraph if needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scenario_id": {
                    "type": "string",
                    "description": "Scenario to solve for.",
                },
            },
            "required": ["scenario_id"],
        },
    },
}

# Ordered list of all tools exposed to the agent.
_AGENT_TOOLS: list[dict[str, Any]] = [
    GET_SUBGRAPH_TOOL_SCHEMA,
    SOLVER_TOOL_SCHEMA_STANDALONE,
    SEARCH_CONTEXT_TOOL_SCHEMA,
    INGESTION_TOOL_SCHEMA,
]


@dataclass
class _AgentState:
    """Mutable state accumulated across tool calls within a single pipeline run."""

    subgraph: AffectedSubgraph | None = None
    live_state: LiveState | None = None
    solver_result: SolverResult | None = None
    tool_call_trace: list[ToolCallRecord] = field(default_factory=list)


async def run_pipeline(
    request: QueryRequest,
    driver: AsyncDriver,
    pool: asyncpg.Pool,
    llm_client: LLMClientBase,
    solver: Solver | None = None,
) -> QueryResponse:
    """Run the ReAct agent pipeline and return a structured response.

    The LLM decides which tools to call (graph traversal, solver, vector
    search, ingestion) and when it has gathered enough information to answer.

    Parameters
    ----------
    request:
        Incoming query (question + scenario_id).
    driver:
        Neo4j async driver — used READ-ONLY for graph traversal.
    pool:
        asyncpg connection pool — used READ-ONLY for live state.
    llm_client:
        LLMClientBase — used for generate() and vector_search().
    solver:
        Optional Solver override.  Defaults to StubSolver.
    """
    _solver: Solver = solver or StubSolver()
    state = _AgentState()

    logger.info(
        "ReAct pipeline start: scenario=%s question=%r",
        request.scenario_id,
        request.question[:80],
    )

    messages: list[Message] = [
        Message(role="system", content=_SYSTEM_PROMPT),
        Message(
            role="user",
            content=(
                f"SCENARIO: {request.scenario_id}\n\n"
                f"QUESTION: {request.question}\n\n"
                "Use the available tools to investigate this scenario, then provide "
                "a specific, actionable answer."
            ),
        ),
    ]

    final_result = None

    for round_num in range(_MAX_AGENT_ROUNDS + 1):
        result = await llm_client.generate(messages, tools=_AGENT_TOOLS)
        final_result = result

        if not result.tool_calls or round_num == _MAX_AGENT_ROUNDS:
            if result.tool_calls and round_num == _MAX_AGENT_ROUNDS:
                logger.warning(
                    "ReAct pipeline: safety cap (%d rounds) reached for scenario=%s; "
                    "forcing final generation without tools.",
                    _MAX_AGENT_ROUNDS,
                    request.scenario_id,
                )
                final_result = await llm_client.generate(messages, tools=None)
            break

        # Append the assistant turn with its tool calls.
        messages.append(
            Message(
                role="assistant",
                content=result.content,
                tool_calls=result.tool_calls,
            )
        )

        # Dispatch each tool and feed the result back.
        for tc in result.tool_calls:
            logger.info(
                "ReAct tool call: round=%d tool=%s args=%s",
                round_num + 1,
                tc.tool_name,
                tc.arguments,
            )
            output = await _dispatch_tool(
                tc,
                driver=driver,
                pool=pool,
                llm_client=llm_client,
                solver=_solver,
                state=state,
                scenario_id=request.scenario_id,
            )
            state.tool_call_trace.append(
                ToolCallRecord(
                    tool_name=tc.tool_name,
                    arguments=tc.arguments,
                    output=output,
                )
            )
            messages.append(
                Message(
                    role="tool",
                    content=json.dumps(output),
                    tool_call_id=tc.call_id,
                )
            )

    answer = (final_result.content if final_result else None) or _fallback_answer(
        state, request.scenario_id
    )

    logger.info(
        "ReAct pipeline complete: scenario=%s tool_rounds=%d answer_len=%d",
        request.scenario_id,
        len(state.tool_call_trace),
        len(answer),
    )

    return QueryResponse(
        question=request.question,
        scenario_id=request.scenario_id,
        answer=answer,
        affected_entities=(
            state.subgraph.affected_entity_ids if state.subgraph else []
        ),
        solver=_build_solver_out(state.solver_result),
        tool_call_trace=state.tool_call_trace,
    )


# ── Tool dispatcher ───────────────────────────────────────────────────────────


async def _dispatch_tool(
    tc: ToolCall,
    *,
    driver: AsyncDriver,
    pool: asyncpg.Pool,
    llm_client: LLMClientBase,
    solver: Solver,
    state: _AgentState,
    scenario_id: str,
) -> dict[str, Any]:
    """Route a tool call to the correct callable, update agent state, return JSON result."""

    if tc.tool_name == "get_affected_subgraph":
        sid = tc.arguments.get("scenario_id", scenario_id)
        # Call run_stage1 once and both cache the Python object and serialise for the LLM.
        try:
            subgraph = await run_stage1(sid, driver)
            state.subgraph = subgraph
            return {
                "success": True,
                "scenario_id": subgraph.scenario_id,
                "affected_entity_count": len(subgraph.affected_entity_ids),
                "entity_ids": subgraph.affected_entity_ids,
                "dependency_edges": [
                    {"from_id": f, "to_id": t, "type": et}
                    for f, t, et in subgraph.dependency_edges
                ],
                "entity_details": {
                    eid: {k: v for k, v in attrs.items() if k != "id"}
                    for eid, attrs in subgraph.entity_attributes.items()
                },
            }
        except Exception as exc:
            logger.exception("get_affected_subgraph tool failed: scenario=%s", sid)
            return {"success": False, "scenario_id": sid, "error": str(exc)}

    if tc.tool_name == "solve_impact":
        sid = tc.arguments.get("scenario_id", scenario_id)
        # Ensure the subgraph is available (run Stage 1 if the agent skipped it).
        if state.subgraph is None or state.subgraph.scenario_id != sid:
            state.subgraph = await run_stage1(sid, driver)
        live_state, solver_result = await run_stage2(state.subgraph, pool, solver)
        state.live_state = live_state
        state.solver_result = solver_result
        return {
            "success": True,
            "scenario_id": sid,
            "affected_count": solver_result.affected_count,
            "max_chain_length": solver_result.max_chain_length,
            "impact_score": solver_result.impact_score,
            "response_options": [
                {
                    "rank": opt.rank,
                    "label": opt.label,
                    "description": opt.description,
                    "estimated_impact_reduction": opt.estimated_impact_reduction,
                }
                for opt in solver_result.response_options
            ],
            "explanation": solver_result.explanation,
        }

    if tc.tool_name == "search_scenario_context":
        return await call_search_tool(tc.arguments, llm_client)

    if tc.tool_name == "run_ingestion_pull":
        return await call_ingestion_tool(tc.arguments, pool, neo4j_driver=driver)

    return {"success": False, "error": f"Unknown tool '{tc.tool_name}'."}


# ── Response helpers ──────────────────────────────────────────────────────────


def _build_solver_out(solver_result: SolverResult | None) -> SolverResultOut:
    """Convert an optional SolverResult to the API output type."""
    if solver_result is None:
        return SolverResultOut(
            affected_count=0,
            max_chain_length=0,
            impact_score=0.0,
            response_options=[],
            explanation="The solver was not invoked for this query.",
        )
    return SolverResultOut(
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
    )


def _fallback_answer(state: _AgentState, scenario_id: str) -> str:
    entity_count = len(state.subgraph.affected_entity_ids) if state.subgraph else 0
    impact = f"{state.solver_result.impact_score:.3f}" if state.solver_result else "unknown"
    return (
        f"[Synthesis unavailable — pipeline completed without LLM text output] "
        f"Scenario '{scenario_id}' affects {entity_count} "
        f"entit{'y' if entity_count == 1 else 'ies'} with impact score {impact}."
    )
