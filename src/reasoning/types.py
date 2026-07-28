"""Reasoning pipeline request/response types (Pydantic models for the API)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(
        ...,
        description="Natural-language question about the simulation scenario.",
    )
    scenario_id: str = Field(
        ...,
        description=(
            "ID of the simulation scenario to reason about.  All events "
            "injected under this scenario_id form the overlay."
        ),
    )


class ResponseOptionOut(BaseModel):
    rank: int
    label: str
    description: str
    estimated_impact_reduction: float


class EntityValueOut(BaseModel):
    entity_id: str
    value_usd: float


class RecommendedRerouteOut(BaseModel):
    entity_id: str
    target_id: str
    target_label: str
    latitude: float
    longitude: float
    rationale: str = ""


class SolverResultOut(BaseModel):
    affected_count: int
    max_chain_length: int
    impact_score: float
    total_value_at_risk: float = 0.0
    currency: str = "USD"
    value_breakdown: list[EntityValueOut] = Field(default_factory=list)
    response_options: list[ResponseOptionOut]
    recommended_reroutes: list[RecommendedRerouteOut] = Field(default_factory=list)
    explanation: str


class ToolCallRecord(BaseModel):
    """A single tool invocation made by the ReAct agent, with its result."""

    tool_name: str
    arguments: dict[str, Any]
    output: dict[str, Any]


class QueryResponse(BaseModel):
    question: str
    scenario_id: str
    answer: str = Field(
        ...,
        description=(
            "LLM-generated explanation grounded in Stage-1 and Stage-2 output. "
            "The LLM explains; it does not invent impact numbers."
        ),
    )
    affected_entities: list[str] = Field(
        ...,
        description="Entity IDs collected by Stage-1 structural traversal.",
    )
    solver: SolverResultOut = Field(
        ...,
        description="Structured Stage-2 solver output (auditable numbers).",
    )
    tool_call_trace: list[ToolCallRecord] = Field(
        default_factory=list,
        description=(
            "Ordered record of every tool the ReAct agent invoked before "
            "producing its final answer.  Empty when the LLM needed no tools."
        ),
    )
