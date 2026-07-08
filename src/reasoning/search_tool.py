"""Tool wrapper for pgvector scenario-context search.

Registers ``search_scenario_context`` as a callable tool so the ReAct agent
can retrieve event narrative chunks from the vector store mid-reasoning.
The callable delegates to ``LLMClientBase.vector_search()`` exactly —
no logic is duplicated.

Usage in the reasoning pipeline:
    from src.reasoning.search_tool import SEARCH_CONTEXT_TOOL_SCHEMA, call_search_tool

    result = await llm_client.generate(messages, tools=[SEARCH_CONTEXT_TOOL_SCHEMA])

    if result.tool_calls:
        output = await call_search_tool(result.tool_calls[0].arguments, llm_client)
"""
from __future__ import annotations

import logging
from typing import Any

from src.llm.base import LLMClientBase

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema (passed as an element of the ``tools`` list in generate())
# ---------------------------------------------------------------------------

SEARCH_CONTEXT_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "search_scenario_context",
        "description": (
            "Search the scenario's vector store for event descriptions relevant "
            "to the query.  Returns ranked text chunks describing what happened "
            "in the simulation event (closures, disruptions, injected overlays).  "
            "Call this to ground your answer in the actual event narrative before "
            "responding to the user."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language search query to match against stored event descriptions.",
                },
                "scenario_id": {
                    "type": "string",
                    "description": "Scenario whose vector store to search.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of chunks to return (default: 3).",
                    "default": 3,
                },
            },
            "required": ["query", "scenario_id"],
        },
    },
}

# ---------------------------------------------------------------------------
# Tool callable (dispatched by the ReAct orchestrator)
# ---------------------------------------------------------------------------


async def call_search_tool(
    arguments: dict[str, Any],
    llm_client: LLMClientBase,
) -> dict[str, Any]:
    """Execute the vector search tool call requested by the LLM.

    Returns a JSON-serialisable dict the orchestrator feeds back as a tool
    response message.
    """
    query: str = arguments.get("query", "")
    scenario_id: str = arguments.get("scenario_id", "")
    top_k: int = int(arguments.get("top_k", 3))

    if not query or not scenario_id:
        return {"success": False, "error": "Both 'query' and 'scenario_id' are required."}

    vector_db_id = f"sim_events_{scenario_id}"
    try:
        chunks = await llm_client.vector_search(query, vector_db_id, top_k=top_k)
        if not chunks:
            return {
                "success": True,
                "scenario_id": scenario_id,
                "chunks": [],
                "note": "No event descriptions found in the vector store for this scenario.",
            }
        return {
            "success": True,
            "scenario_id": scenario_id,
            "chunks": [
                {"content": c.content, "score": round(c.score, 4), "document_id": c.document_id}
                for c in chunks
            ],
        }
    except Exception as exc:
        logger.exception("Search tool call failed: scenario=%s query=%r", scenario_id, query)
        return {"success": False, "scenario_id": scenario_id, "error": str(exc)}
