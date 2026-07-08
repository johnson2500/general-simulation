"""Shared domain types for the LLM client surface.

These are thin, backend-agnostic data classes.  Concrete client implementations
convert between these and their SDK-specific types internally, so the rest of
the app never imports from any LLM SDK directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """A single turn in a conversation.

    For assistant turns that contain tool invocations, set ``tool_calls`` and
    leave ``content`` as ``None``.  For tool-result turns (role="tool"), set
    ``tool_call_id`` to the ``ToolCall.call_id`` returned by the model so the
    backend can correlate the result with the original request.
    """

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list["ToolCall"] | None = None
    tool_call_id: str | None = None


@dataclass
class ToolCall:
    """A structured tool invocation requested by the model."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class GenerateResult:
    """Result of a single chat completion call."""

    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = "end_of_turn"


@dataclass
class Chunk:
    """A retrieved text chunk from a vector search."""

    document_id: str
    content: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)
