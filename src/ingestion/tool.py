"""Tool wrapper for on-demand ingestion pulls.

Registers the ingestion runner as a callable tool so the ReAct agent pipeline
can request a fresh data pull mid-reasoning.  The tool wraps run_ingestion()
exactly — no logic is duplicated.

Adapters are resolved from ``src.ingestion.registry`` based on
``Settings.enabled_domains``.

Usage in the reasoning pipeline:
    from src.ingestion.tool import get_ingestion_tool_schema, call_ingestion_tool

    schema = get_ingestion_tool_schema()
    result = await llm_client.generate(messages, tools=[schema])

    if result.tool_calls:
        output = await call_ingestion_tool(result.tool_calls[0].arguments, pool)
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg
from neo4j import AsyncDriver

from src.core.config import Settings
from src.ingestion.registry import get_adapter_class, list_adapter_ids
from src.ingestion.runner import run_ingestion

logger = logging.getLogger(__name__)


def get_ingestion_tool_schema(
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Build the ingestion tool schema for currently enabled adapters."""
    adapter_ids = list_adapter_ids(settings)
    supported = ", ".join(repr(a) for a in adapter_ids) or "(none)"
    return {
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
                            f"Supported: {supported}."
                        ),
                        "enum": adapter_ids,
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


# Back-compat name for callers that expect a module-level schema object.
# Built at import time from current Settings / ENABLED_DOMAINS.
INGESTION_TOOL_SCHEMA: dict[str, Any] = get_ingestion_tool_schema()


async def call_ingestion_tool(
    arguments: dict[str, Any],
    pool: asyncpg.Pool,
    neo4j_driver: AsyncDriver | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Execute the ingestion tool call requested by the LLM.

    Returns a JSON-serialisable dict the orchestrator can feed back to the
    model as a tool response message.
    """
    adapter_id: str = arguments.get("adapter_id", "")
    try:
        adapter_cls = get_adapter_class(adapter_id, settings)
    except KeyError as exc:
        return {"success": False, "error": str(exc)}

    try:
        adapter = adapter_cls()
        count = await run_ingestion(adapter, pool, neo4j_driver=neo4j_driver)
        return {"success": True, "adapter_id": adapter_id, "entities_upserted": count}
    except Exception as exc:
        logger.exception("Ingestion tool call failed: adapter=%s", adapter_id)
        return {"success": False, "adapter_id": adapter_id, "error": str(exc)}
