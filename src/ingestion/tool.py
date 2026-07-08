"""Tool wrapper for on-demand ingestion pulls.

Registers the ingestion runner as a callable tool so the ReAct agent pipeline
can request a fresh data pull mid-reasoning.  The tool wraps run_ingestion()
exactly — no logic is duplicated.

Usage in the reasoning pipeline:
    from src.ingestion.tool import INGESTION_TOOL_SCHEMA, call_ingestion_tool

    # Pass schema to generate() so the LLM knows the tool exists:
    result = await llm_client.generate(messages, tools=[INGESTION_TOOL_SCHEMA])

    # When the LLM emits a tool call, dispatch it:
    if result.tool_calls:
        output = await call_ingestion_tool(result.tool_calls[0].arguments, pool)
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg
from neo4j import AsyncDriver

from src.ingestion.adapters.opensky_flights import OpenSkyFlightsAdapter
from src.ingestion.adapters.usgs_earthquakes import USGSEarthquakeAdapter
from src.ingestion.runner import run_ingestion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema (passed as an element of the ``tools`` list in generate())
# ---------------------------------------------------------------------------

INGESTION_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_ingestion_pull",
        "description": (
            "Trigger a fresh data pull from a registered ingestion adapter "
            "and upsert the results into the live store.  "
            "Call this when you need up-to-date ground-truth data before "
            "reasoning about the current state of entities."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "adapter_id": {
                    "type": "string",
                    "description": (
                        "Identifier of the adapter to run.  "
                        "Supported: 'opensky_flights', 'usgs_earthquakes'."
                    ),
                    "enum": ["opensky_flights", "usgs_earthquakes"],
                },
                "force": {
                    "type": "boolean",
                    "description": (
                        "When true, run the pull even if the adapter was "
                        "recently polled.  Defaults to false."
                    ),
                    "default": False,
                },
            },
            "required": ["adapter_id"],
        },
    },
}

# ---------------------------------------------------------------------------
# Tool callable (dispatched by our orchestrator when the LLM calls the tool)
# ---------------------------------------------------------------------------

# Registry maps adapter_id → adapter factory.
# Add new adapters here — the tool callable and schema stay unchanged.
_ADAPTER_REGISTRY: dict[str, Any] = {
    "opensky_flights": OpenSkyFlightsAdapter,
    "usgs_earthquakes": USGSEarthquakeAdapter,
}


async def call_ingestion_tool(
    arguments: dict[str, Any],
    pool: asyncpg.Pool,
    neo4j_driver: AsyncDriver | None = None,
) -> dict[str, Any]:
    """Execute the ingestion tool call requested by the LLM.

    Returns a JSON-serialisable dict the orchestrator can feed back to the
    model as a tool response message.
    """
    adapter_id: str = arguments.get("adapter_id", "")
    adapter_cls = _ADAPTER_REGISTRY.get(adapter_id)

    if adapter_cls is None:
        return {
            "success": False,
            "error": f"Unknown adapter_id '{adapter_id}'. "
                     f"Available: {list(_ADAPTER_REGISTRY)}",
        }

    try:
        adapter = adapter_cls()
        count = await run_ingestion(adapter, pool, neo4j_driver=neo4j_driver)
        return {"success": True, "adapter_id": adapter_id, "entities_upserted": count}
    except Exception as exc:
        logger.exception("Ingestion tool call failed: adapter=%s", adapter_id)
        return {"success": False, "adapter_id": adapter_id, "error": str(exc)}
