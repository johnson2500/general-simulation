"""OpenAI-compatible LLM client.

Uses the openai Python SDK for inference (generate + embed), and talks to
pgvector directly via asyncpg for vector/RAG operations.

Because the openai SDK supports a configurable ``base_url``, this client works
with any OpenAI-compatible inference endpoint — including Llama Stack's /v1 API,
vLLM, Ollama, etc.  Switching endpoints is purely a configuration change:

    LLM_BASE_URL=http://llamastack:8321/v1   # point at Llama Stack
    LLM_BASE_URL=https://api.openai.com/v1   # point at OpenAI (default)
"""
from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg
import openai

from src.core.config import Settings
from src.llm.types import Chunk, GenerateResult, Message, ToolCall

logger = logging.getLogger(__name__)

# Table name for all vector collections.  Each collection is identified by
# its ``collection`` column value so a single table serves every vector_db_id.
_TABLE = "llm_embeddings"


class OpenAIClient:
    """LLM client backed by any OpenAI-compatible inference endpoint + pgvector.

    Inference (generate / embed) → openai.AsyncOpenAI
    Vector / RAG operations      → asyncpg + pgvector SQL
    """

    def __init__(self, settings: Settings, pool: asyncpg.Pool | None = None) -> None:
        self._settings = settings
        self._pool = pool
        self._ai = openai.AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.openai_api_key or "unused",
        )
        self._ensured_dbs: set[str] = set()

    # ── Inference ─────────────────────────────────────────────────────────────

    async def generate(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
    ) -> GenerateResult:
        sdk_messages: list[dict[str, Any]] = []
        for m in messages:
            msg: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.call_id,
                        "type": "function",
                        "function": {
                            "name": tc.tool_name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in m.tool_calls
                ]
            if m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            sdk_messages.append(msg)
        kwargs: dict[str, Any] = dict(
            model=self._settings.generation_model_id,
            messages=sdk_messages,
        )
        if tools:
            kwargs["tools"] = tools

        response = await self._ai.chat.completions.create(**kwargs)
        choice = response.choices[0]
        msg = choice.message

        content: str | None = msg.content
        tool_calls: list[ToolCall] = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        call_id=tc.id,
                        tool_name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        stop_reason = (
            "end_of_message" if choice.finish_reason == "tool_calls" else "end_of_turn"
        )
        return GenerateResult(
            content=content, tool_calls=tool_calls, stop_reason=stop_reason
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._ai.embeddings.create(
            model=self._settings.embedding_model_id,
            input=texts,
        )
        return [item.embedding for item in response.data]

    # ── Vector / RAG ──────────────────────────────────────────────────────────

    async def ensure_vector_db(
        self,
        vector_db_id: str,
        provider_id: str | None = None,
    ) -> None:
        if vector_db_id in self._ensured_dbs:
            return
        pool = self._require_pool("ensure_vector_db")
        dim = self._settings.embedding_dimension
        async with pool.acquire() as conn:
            await conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    id         TEXT,
                    collection TEXT,
                    content    TEXT,
                    metadata   JSONB DEFAULT '{{}}',
                    embedding  vector({dim}),
                    PRIMARY KEY (collection, id)
                )
            """)
        self._ensured_dbs.add(vector_db_id)
        logger.debug("Ensured vector collection '%s'", vector_db_id)

    async def ingest_documents(
        self,
        documents: list[dict[str, Any]],
        vector_db_id: str,
    ) -> None:
        await self.ensure_vector_db(vector_db_id)
        pool = self._require_pool("ingest_documents")

        contents = [doc["content"] for doc in documents]
        embeddings = await self.embed(contents)

        async with pool.acquire() as conn:
            for doc, embedding in zip(documents, embeddings):
                await conn.execute(
                    f"""
                    INSERT INTO {_TABLE} (id, collection, content, metadata, embedding)
                    VALUES ($1, $2, $3, $4, $5::vector)
                    ON CONFLICT (collection, id) DO UPDATE
                        SET content   = EXCLUDED.content,
                            metadata  = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding
                    """,
                    doc["id"],
                    vector_db_id,
                    doc["content"],
                    json.dumps(doc.get("metadata", {})),
                    json.dumps(embedding),
                )

        logger.debug(
            "Ingested %d documents into collection '%s'",
            len(documents),
            vector_db_id,
        )

    async def vector_search(
        self,
        query: str,
        vector_db_id: str,
        top_k: int = 5,
    ) -> list[Chunk]:
        await self.ensure_vector_db(vector_db_id)
        pool = self._require_pool("vector_search")

        query_embedding = (await self.embed([query]))[0]

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, content, metadata,
                       1 - (embedding <=> $1::vector) AS score
                FROM {_TABLE}
                WHERE collection = $2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                json.dumps(query_embedding),
                vector_db_id,
                top_k,
            )

        return [
            Chunk(
                document_id=row["id"],
                content=row["content"],
                score=float(row["score"]),
                metadata=dict(row["metadata"] or {}),
            )
            for row in rows
        ]

    async def unregister_vector_db(self, vector_db_id: str) -> None:
        pool = self._require_pool("unregister_vector_db")
        async with pool.acquire() as conn:
            deleted = await conn.fetchval(
                f"DELETE FROM {_TABLE} WHERE collection = $1 RETURNING count(*)",
                vector_db_id,
            )
        self._ensured_dbs.discard(vector_db_id)
        logger.debug(
            "Removed vector collection '%s' (%s rows deleted)",
            vector_db_id,
            deleted,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _require_pool(self, op: str) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError(
                f"OpenAIClient.{op}() requires a database pool but pool is None. "
                "Ensure the DB pool was created successfully at startup."
            )
        return self._pool
