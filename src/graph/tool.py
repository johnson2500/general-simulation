"""Tool wrapper for the Stage-1 Neo4j subgraph traversal.

Registers ``get_affected_subgraph`` as a callable tool so the ReAct agent can
request a graph traversal mid-reasoning.  The callable delegates to
``run_stage1()`` exactly — no logic is duplicated.

Usage in the reasoning pipeline:
    from src.graph.tool import GET_SUBGRAPH_TOOL_SCHEMA, call_subgraph_tool

    result = await llm_client.generate(messages, tools=[GET_SUBGRAPH_TOOL_SCHEMA])

    if result.tool_calls:
        output = await call_subgraph_tool(result.tool_calls[0].arguments, driver)
"""
from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncDriver

from src.reasoning.stage1 import run_stage1

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema (passed as an element of the ``tools`` list in generate())
# ---------------------------------------------------------------------------

GET_SUBGRAPH_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "get_affected_subgraph",
        "description": (
            "Traverse the Neo4j dependency graph to discover which entities are "
            "affected by the simulation scenario and how they are connected.  "
            "Returns affected entity IDs, dependency edges (type + direction), "
            "and entity attributes (callsign, route, origin, status).  "
            "Call this FIRST to understand the scope before running the solver."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "scenario_id": {
                    "type": "string",
                    "description": "ID of the simulation scenario to query.",
                },
            },
            "required": ["scenario_id"],
        },
    },
}

# ---------------------------------------------------------------------------
# Tool callable (dispatched by the ReAct orchestrator)
# ---------------------------------------------------------------------------


async def call_subgraph_tool(
    arguments: dict[str, Any],
    driver: AsyncDriver,
) -> dict[str, Any]:
    """Execute the subgraph traversal tool call requested by the LLM.

    Returns a JSON-serialisable dict the orchestrator feeds back as a tool
    response message.  The full ``AffectedSubgraph`` Python object is NOT
    returned here — the orchestrator retains it in its internal state for
    subsequent tool calls (e.g. ``solve_impact``).
    """
    scenario_id: str = arguments.get("scenario_id", "")
    if not scenario_id:
        return {"success": False, "error": "scenario_id is required."}

    try:
        subgraph = await run_stage1(scenario_id, driver)
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
        logger.exception("Subgraph tool call failed: scenario=%s", scenario_id)
        return {"success": False, "scenario_id": scenario_id, "error": str(exc)}
