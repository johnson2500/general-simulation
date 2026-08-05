"""Llama Stack tool wrapper for the Stage-2 solver.

Registers ``solve()`` as a callable tool so the LLM can request a fresh
quantitative solve mid-reasoning.  Same wrap-don't-duplicate rule as the
ingestion tool: the callable delegates straight to ``StubSolver.solve()``
(or whatever solver is injected) — no logic is repeated here.

Usage in the reasoning pipeline (Phase 7):
    from src.solver.tool import SOLVER_TOOL_SCHEMA, call_solver_tool

    # Offer the tool to the LLM:
    result = await llm_client.generate(messages, tools=[SOLVER_TOOL_SCHEMA])

    # When the LLM emits a tool call, dispatch it:
    if result.tool_calls:
        output = await call_solver_tool(
            result.tool_calls[0].arguments,
            subgraph,
            live_state,
        )
"""
from __future__ import annotations

import logging
from typing import Any

from src.core.solver import AffectedSubgraph, LiveState, Solver, SolverResult
from src.solver.stub import StubSolver

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema (passed in the ``tools`` list to generate())
# ---------------------------------------------------------------------------

SOLVER_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "solve_impact",
        "description": (
            "Run the Stage-2 quantitative solver on the affected subgraph for "
            "a simulation event.  Returns impact score, affected entity count, "
            "longest dependency chain length, total economic value at risk, "
            "ranked response options, and recommended reroute targets "
            "(alternate locations with coordinates for map display).  "
            "Call this when you need structured numbers to support your "
            "impact explanation — do NOT invent figures yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "ID of the simulation event to solve for.",
                },
                "scenario_id": {
                    "type": "string",
                    "description": "Scenario this event belongs to.",
                },
            },
            "required": ["event_id", "scenario_id"],
        },
    },
}


# ---------------------------------------------------------------------------
# Tool callable
# ---------------------------------------------------------------------------


async def call_solver_tool(
    arguments: dict[str, Any],
    subgraph: AffectedSubgraph,
    live_state: LiveState,
    solver: Solver | None = None,
) -> dict[str, Any]:
    """Execute the solver tool call requested by the LLM.

    ``subgraph`` and ``live_state`` must already be available (computed in
    Stage 1 of the reasoning pipeline before the tool is offered to the LLM).

    ``solver`` defaults to ``StubSolver()``.  Inject a domain-specific solver
    by passing it explicitly.

    Returns a JSON-serialisable dict the orchestrator feeds back as a tool
    response message.
    """
    if solver is None:
        solver = StubSolver()

    event_id: str = arguments.get("event_id", subgraph.event_id)

    try:
        result: SolverResult = solver.solve(subgraph, live_state)
        return {
            "success": True,
            "event_id": event_id,
            "affected_count": result.affected_count,
            "max_chain_length": result.max_chain_length,
            "impact_score": result.impact_score,
            "total_value_at_risk": result.total_value_at_risk,
            "currency": result.currency,
            "value_breakdown": result.value_breakdown,
            "response_options": [
                {
                    "rank": opt.rank,
                    "label": opt.label,
                    "description": opt.description,
                    "estimated_impact_reduction": opt.estimated_impact_reduction,
                }
                for opt in result.response_options
            ],
            "recommended_reroutes": [
                {
                    "entity_id": r.entity_id,
                    "target_id": r.target_id,
                    "target_label": r.target_label,
                    "latitude": r.latitude,
                    "longitude": r.longitude,
                    "rationale": r.rationale,
                }
                for r in result.recommended_reroutes
            ],
            "explanation": result.explanation,
        }
    except Exception as exc:
        logger.exception("Solver tool call failed: event_id=%s", event_id)
        return {"success": False, "event_id": event_id, "error": str(exc)}
