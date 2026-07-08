"""Stage 3 — Synthesis: vector retrieval + LLM generation.

A standalone synthesis helper.  In the ReAct pipeline (pipeline.py) the LLM
calls tools directly and generates its final answer as part of the agent loop,
so this module is no longer invoked by the pipeline.

It remains useful for:
  • unit tests that need a quick generate() call with pre-built context
  • alternative callers that have already run Stage 1 and Stage 2 and just
    want a single grounded completion

The LLM receives:
  - The user's question
  - Retrieved event descriptions (vector_search against the scenario DB)
  - Stage-1 structural facts (entity count, dependency chain)
  - Stage-2 solver numbers (impact score, response options)

The LLM EXPLAINS the impact using these numbers.
It must NOT invent figures beyond what Stages 1 & 2 produced.

All LLM and vector calls go through the LLMClientBase — never directly
to individual backends.
"""
from __future__ import annotations

import logging
from typing import Any

from src.core.solver import AffectedSubgraph, SolverResult
from src.llm.base import LLMClientBase
from src.llm.types import Message

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are an expert operational impact analyst specialising in aviation and logistics disruptions.
Your role is to give clear, actionable, domain-specific answers grounded in the provided data.

Rules:
- Use the entity details table (callsign, route, origin, status) to refer to aircraft by name, not by raw ID.
- Use the Stage-1 and Stage-2 figures as the authoritative structural and quantitative facts — do not contradict them.
- You MAY apply domain knowledge (standard diversion airports, NATS track procedures, ATC protocols) to enrich your answer.
- For rerouting questions, name specific alternate airports, tracks, or procedures. Estimate revised arrival times using typical flight durations where departure time is given.
- Be concise but specific. Bullet points are encouraged for action items.\
"""


async def run_stage3(
    question: str,
    subgraph: AffectedSubgraph,
    solver_result: SolverResult,
    llm_client: LLMClientBase,
) -> tuple[str, list]:
    """Retrieve vector context and generate a grounded answer.

    Returns ``(answer, [])`` — the empty list is the tool-call trace kept for
    API compatibility.  This function makes a single generate() call without
    tool use; for multi-tool reasoning use the ReAct pipeline instead.
    """
    vector_db_id = f"sim_events_{subgraph.scenario_id}"

    chunks = await llm_client.vector_search(question, vector_db_id, top_k=3)
    vector_context = (
        "\n\n".join(c.content for c in chunks)
        if chunks
        else "No event context found in the vector store for this scenario."
    )

    user_message = _build_user_message(question, subgraph, solver_result, vector_context)

    messages = [
        Message(role="system", content=_SYSTEM_PROMPT),
        Message(role="user", content=user_message),
    ]

    result = await llm_client.generate(messages)

    answer = result.content or ""
    if not answer:
        logger.warning(
            "Stage 3: generate() returned no content (stop_reason=%s)",
            result.stop_reason,
        )
        answer = (
            f"[Synthesis unavailable] "
            f"Scenario '{subgraph.scenario_id}' affects {solver_result.affected_count} "
            f"entit{'y' if solver_result.affected_count == 1 else 'ies'} "
            f"with impact score {solver_result.impact_score:.3f}."
        )

    logger.info(
        "Stage 3 complete: scenario=%s answer_len=%d",
        subgraph.scenario_id,
        len(answer),
    )
    return answer, []


def _format_entity_table(entity_attributes: dict[str, dict[str, Any]]) -> str:
    """Render entity node properties as a readable table for the LLM prompt."""
    if not entity_attributes:
        return "  (no entity details available)"
    lines = []
    for eid, attrs in entity_attributes.items():
        callsign = attrs.get("callsign", "—")
        route = attrs.get("route", "—")
        origin = attrs.get("origin", "—")
        status = attrs.get("status", attrs.get("type", "—"))
        lines.append(
            f"  • {callsign:<10} id={eid}  route={route}  origin={origin}  status={status}"
        )
    return "\n".join(lines)


def _build_user_message(
    question: str,
    subgraph: AffectedSubgraph,
    solver_result: SolverResult,
    vector_context: str,
) -> str:
    entity_table = _format_entity_table(subgraph.entity_attributes)

    options_text = "\n".join(
        f"  {opt.rank}. [{opt.label}] {opt.description} "
        f"(estimated impact reduction: {opt.estimated_impact_reduction:.0%})"
        for opt in solver_result.response_options
    )

    return f"""\
QUESTION: {question}

─── EVENT CONTEXT (scenario description) ────────────────────────────────────
{vector_context}

─── STAGE-1: AFFECTED ENTITIES (graph traversal — deterministic) ────────────
Scenario:              {subgraph.scenario_id}
Total affected:        {solver_result.affected_count}
Longest dep. chain:    {solver_result.max_chain_length} hop(s)

Entity details:
{entity_table}

─── STAGE-2: QUANTITATIVE ANALYSIS (solver) ─────────────────────────────────
Impact score:          {solver_result.impact_score:.4f}  (1.0 = maximum severity)

Response options (ranked):
{options_text if options_text else "  (none)"}

─────────────────────────────────────────────────────────────────────────────
Answer the question using the entity details and event context above.
Refer to aircraft by callsign. For rerouting questions, name specific diversion
airports and estimate revised arrival times using standard flight durations.\
"""
