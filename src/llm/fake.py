"""FakeLLMClient — in-memory stub for tests and GPU-free dev.

Activated by setting LLM_BACKEND=fake in the environment (or .env).
Returns deterministic canned responses so the full reasoning pipeline can be
exercised without a running LLM server or GPU.
"""
from __future__ import annotations

import math
from typing import Any

from src.core.config import Settings
from src.llm.types import Chunk, GenerateResult, Message, ToolCall

DEFAULT_COMPLETION = (
    "This is a canned response from FakeLLMClient. "
    "Set LLM_BACKEND=openai (or llamastack) and configure credentials for real inference."
)


class FakeLLMClient:
    """Pure-Python drop-in for OpenAIClient, no network or GPU required.

    Vector store is backed by an in-memory dict keyed by vector_db_id.
    Similarity scoring uses cosine similarity on fake embeddings so
    vector_search returns sensible ranked results from previously ingested docs.

    Two ways to control ``generate()`` output in tests:

    1. ``response_sequence`` — ordered list of ``GenerateResult`` objects.
       Each call to ``generate()`` pops and returns the next item.  When the
       sequence is exhausted the client falls back to the canned completion.
       Use this for multi-step ReAct tests.

    2. ``canned_tool_calls`` — emitted once (then cleared) when tools are
       offered; subsequent calls return the canned completion.  Use this for
       single-round tool-call tests (Step 1 style).
    """

    def __init__(
        self,
        settings: Settings,
        pool: Any = None,
        canned_completion: str = DEFAULT_COMPLETION,
        canned_tool_calls: list[ToolCall] | None = None,
        response_sequence: list[GenerateResult] | None = None,
    ) -> None:
        self._settings = settings
        self._canned_completion = canned_completion
        self._canned_tool_calls: list[ToolCall] = canned_tool_calls or []
        self._response_sequence: list[GenerateResult] = list(response_sequence or [])
        # {vector_db_id: list[{"id", "content", "metadata", "embedding"}]}
        self._store: dict[str, list[dict[str, Any]]] = {}

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> GenerateResult:
        # Multi-step sequence takes priority (for ReAct pipeline tests).
        if self._response_sequence:
            return self._response_sequence.pop(0)

        if self._canned_tool_calls and tools:
            # Emit the queued tool calls once, then clear so the next call
            # returns the canned completion (simulates a single tool round-trip).
            pending = self._canned_tool_calls
            self._canned_tool_calls = []
            return GenerateResult(
                content=None,
                tool_calls=pending,
                stop_reason="end_of_message",
            )
        return GenerateResult(
            content=self._canned_completion,
            tool_calls=[],
            stop_reason="end_of_turn",
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return deterministic unit vectors derived from each text's hash."""
        dim = self._settings.embedding_dimension
        return [_hash_embed(text, dim) for text in texts]

    async def ingest_documents(
        self,
        documents: list[dict[str, Any]],
        vector_db_id: str,
    ) -> None:
        store = self._store.setdefault(vector_db_id, [])
        dim = self._settings.embedding_dimension
        for doc in documents:
            store.append(
                {
                    "id": doc["id"],
                    "content": doc["content"],
                    "metadata": doc.get("metadata", {}),
                    "embedding": _hash_embed(doc["content"], dim),
                }
            )

    async def vector_search(
        self,
        query: str,
        vector_db_id: str,
        top_k: int = 5,
    ) -> list[Chunk]:
        store = self._store.get(vector_db_id, [])
        if not store:
            return []
        dim = self._settings.embedding_dimension
        q_vec = _hash_embed(query, dim)
        scored = [
            (entry, _cosine(q_vec, entry["embedding"])) for entry in store
        ]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [
            Chunk(
                document_id=entry["id"],
                content=entry["content"],
                score=score,
                metadata=dict(entry["metadata"]),
            )
            for entry, score in scored[:top_k]
        ]

    async def ensure_vector_db(
        self,
        vector_db_id: str,
        provider_id: str | None = None,
    ) -> None:
        self._store.setdefault(vector_db_id, [])

    async def unregister_vector_db(self, vector_db_id: str) -> None:
        self._store.pop(vector_db_id, None)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _hash_embed(text: str, dim: int) -> list[float]:
    """Deterministic unit vector seeded by the hash of *text*.

    Not a real embedding — reproducible vector for testing that gives
    distinct, stable representations for distinct inputs.
    """
    seed = hash(text) & 0xFFFFFFFF
    vec: list[float] = []
    for i in range(dim):
        seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
        vec.append((seed / 0xFFFFFFFF) * 2.0 - 1.0)
    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / mag for x in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a)) or 1.0
    mag_b = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (mag_a * mag_b)
