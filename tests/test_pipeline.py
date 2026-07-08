"""Phase 7 — Three-stage reasoning pipeline tests.

Coverage:
  - Stage 1 (stage1.py): structural AGE traversal
  - Stage 2 (stage2.py): live state read + solver
  - Stage 3 (stage3.py): vector retrieval + LLM synthesis
  - Pipeline orchestrator (pipeline.py): end-to-end wiring
  - POST /query endpoint (api/query.py): full HTTP round-trip

No live DB or GPU required:
  - asyncpg pool/connection are mocked (MagicMock / AsyncMock)
  - FakeLLMClient provides in-memory vector search and generation
  - StubSolver provides deterministic quantitative output
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.deps import get_llm_client, get_neo4j_driver, get_pool, get_solver
from src.core.config import Settings
from src.core.solver import AffectedSubgraph, EntityState
from src.llm.fake import FakeLLMClient
from src.llm.types import GenerateResult, ToolCall
from src.reasoning.pipeline import run_pipeline
from src.reasoning.stage1 import run_stage1
from src.reasoning.stage2 import run_stage2
from src.reasoning.stage3 import run_stage3
from src.reasoning.types import QueryRequest
from src.solver.stub import StubSolver


# ── Test constants ─────────────────────────────────────────────────────────────

SCENARIO_ID = "test_scenario_phase7"
ENTITY_A = "entity-A"
ENTITY_B = "entity-B"
ENTITY_C = "entity-C"
ALL_ENTITIES = [ENTITY_A, ENTITY_B, ENTITY_C]
QUESTION = "What is the expected impact on downstream entities?"

# Edges: A →DEPENDS_ON→ B →FEEDS→ C  (chain length 2)
EDGES = [
    (ENTITY_A, ENTITY_B, "DEPENDS_ON"),
    (ENTITY_B, ENTITY_C, "FEEDS"),
]


# ── Mock helpers ───────────────────────────────────────────────────────────────


def _settings() -> Settings:
    return Settings(
        postgres_dsn="postgresql://mock:mock@localhost/mock",
        llm_backend="fake",
        embedding_dimension=16,
    )


def _fake_client() -> FakeLLMClient:
    return FakeLLMClient(settings=_settings())


def _react_sequence(scenario_id: str = SCENARIO_ID) -> list[GenerateResult]:
    """Standard ReAct response_sequence: subgraph call → solver call → final answer.

    Use this to make the FakeLLMClient simulate a full agent investigation
    before answering.
    """
    return [
        GenerateResult(
            content=None,
            tool_calls=[
                ToolCall(
                    call_id="tc-sg",
                    tool_name="get_affected_subgraph",
                    arguments={"scenario_id": scenario_id},
                )
            ],
            stop_reason="end_of_message",
        ),
        GenerateResult(
            content=None,
            tool_calls=[
                ToolCall(
                    call_id="tc-sol",
                    tool_name="solve_impact",
                    arguments={"scenario_id": scenario_id},
                )
            ],
            stop_reason="end_of_message",
        ),
        GenerateResult(
            content="The scenario affects 3 entities with a high impact score.",
            tool_calls=[],
            stop_reason="end_of_turn",
        ),
    ]


def _conn() -> AsyncMock:
    """Mock asyncpg connection with a no-op fetch by default."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    return conn


def _pool(conn: AsyncMock) -> MagicMock:
    """Mock asyncpg pool that yields *conn* from every acquire()."""
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


# ── Neo4j driver mock ──────────────────────────────────────────────────────────


def _neo4j_driver(*run_responses: list[dict[str, Any]]) -> MagicMock:
    """Return a mock Neo4j AsyncDriver whose session.run() yields each response in order.

    Each positional argument is a list of record dicts returned by ``result.data()``
    for successive ``session.run()`` calls within a single session context.
    """
    responses = list(run_responses)
    response_iter = iter(responses)

    async def _run(*_args: Any, **_kwargs: Any) -> AsyncMock:
        result = AsyncMock()
        result.data = AsyncMock(return_value=next(response_iter, []))
        return result

    session_mock = AsyncMock()
    session_mock.run.side_effect = _run

    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session_mock)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    return driver


def _neo4j_entity_rows(*entity_ids: str) -> list[dict[str, Any]]:
    """Neo4j records for the RETURN n.id AS entity_id query."""
    return [{"entity_id": eid} for eid in entity_ids]


def _neo4j_edge_rows(*edges: tuple[str, str, str]) -> list[dict[str, Any]]:
    """Neo4j records for the edge traversal query."""
    return [{"from_id": f, "edge_type": et, "to_id": t} for f, t, et in edges]


def _neo4j_attr_rows(*entity_ids: str) -> list[dict[str, Any]]:
    """Neo4j records for the entity attributes query."""
    return [{"props": {"id": eid, "callsign": eid, "type": "aircraft"}} for eid in entity_ids]


# ── asyncpg row factories ──────────────────────────────────────────────────────


def _live_state_rows(*specs: tuple[str, str]) -> list[dict[str, Any]]:
    """PostGIS live state rows for (entity_id, status) pairs."""
    return [
        {
            "id": eid,
            "type": "moving_entity",
            "entity_attrs": {},
            "status": status,
            "state_attrs": {},
        }
        for eid, status in specs
    ]


def _standard_neo4j_driver() -> MagicMock:
    """Neo4j driver returning the standard 3-entity scenario for Stage 1."""
    return _neo4j_driver(
        _neo4j_entity_rows(*ALL_ENTITIES),          # MATCH affected entities
        _neo4j_edge_rows(*EDGES),                    # MATCH dependency edges
        _neo4j_attr_rows(*ALL_ENTITIES),             # MATCH entity attributes
    )


def _standard_pool_side_effect() -> list[list[dict]]:
    """asyncpg fetch responses for Stage 2 live state (Stage 1 now uses Neo4j)."""
    return [
        _live_state_rows(
            (ENTITY_A, "operational"),
            (ENTITY_B, "operational"),
            (ENTITY_C, "degraded"),
        ),
    ]


# ── Stage 1 unit tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage1_returns_empty_subgraph_when_no_events():
    # Neo4j returns no entities — driver yields empty list on first run()
    driver = _neo4j_driver([])  # no entities

    subgraph = await run_stage1(SCENARIO_ID, driver)

    assert subgraph.affected_entity_ids == []
    assert subgraph.dependency_edges == []
    assert subgraph.scenario_id == SCENARIO_ID


@pytest.mark.asyncio
async def test_stage1_returns_affected_entities_and_edges():
    driver = _neo4j_driver(
        _neo4j_entity_rows(*ALL_ENTITIES),   # MATCH affected entities
        _neo4j_edge_rows(*EDGES),            # MATCH dependency edges
        _neo4j_attr_rows(*ALL_ENTITIES),     # MATCH entity attributes
    )

    subgraph = await run_stage1(SCENARIO_ID, driver)

    assert set(subgraph.affected_entity_ids) == set(ALL_ENTITIES)
    assert len(subgraph.dependency_edges) == 2
    edge_set = {(f, t, et) for f, t, et in subgraph.dependency_edges}
    assert (ENTITY_A, ENTITY_B, "DEPENDS_ON") in edge_set
    assert (ENTITY_B, ENTITY_C, "FEEDS") in edge_set
    assert subgraph.scenario_id == SCENARIO_ID


@pytest.mark.asyncio
async def test_stage1_skips_malformed_edge_rows():
    driver = _neo4j_driver(
        _neo4j_entity_rows(ENTITY_A, ENTITY_B),
        # One valid edge + one row with None values (should be skipped)
        [
            {"from_id": ENTITY_A, "edge_type": "DEPENDS_ON", "to_id": ENTITY_B},
            {"from_id": None, "edge_type": None, "to_id": None},
        ],
        _neo4j_attr_rows(ENTITY_A, ENTITY_B),
    )

    subgraph = await run_stage1(SCENARIO_ID, driver)

    assert len(subgraph.dependency_edges) == 1


# ── Stage 2 unit tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage2_returns_live_state_and_solver_result():
    conn = _conn()
    conn.fetch.return_value = _live_state_rows(
        (ENTITY_A, "operational"), (ENTITY_B, "operational")
    )
    pool = _pool(conn)

    subgraph = AffectedSubgraph(
        event_id="scenario:s1",
        scenario_id="s1",
        affected_entity_ids=[ENTITY_A, ENTITY_B],
        dependency_edges=[(ENTITY_A, ENTITY_B, "DEPENDS_ON")],
    )

    live_state, result = await run_stage2(subgraph, pool, StubSolver())

    assert ENTITY_A in live_state
    assert ENTITY_B in live_state
    assert live_state[ENTITY_A].status == "operational"
    assert result.affected_count == 2
    assert result.max_chain_length >= 1


@pytest.mark.asyncio
async def test_stage2_seeds_unknown_state_for_missing_entities():
    """Entities not in the DB should get a default 'unknown' state."""
    conn = _conn()
    # DB returns only ENTITY_A; ENTITY_B is missing
    conn.fetch.return_value = _live_state_rows((ENTITY_A, "operational"))
    pool = _pool(conn)

    subgraph = AffectedSubgraph(
        event_id="s",
        scenario_id="s",
        affected_entity_ids=[ENTITY_A, ENTITY_B],
    )

    live_state, _ = await run_stage2(subgraph, pool, StubSolver())

    assert live_state[ENTITY_A].status == "operational"
    assert live_state[ENTITY_B].status == "unknown"


@pytest.mark.asyncio
async def test_stage2_empty_entity_list():
    conn = _conn()
    pool = _pool(conn)

    subgraph = AffectedSubgraph(
        event_id="s", scenario_id="s", affected_entity_ids=[]
    )

    live_state, result = await run_stage2(subgraph, pool, StubSolver())

    assert live_state == {}
    assert result.affected_count == 0
    conn.fetch.assert_not_called()


# ── Stage 3 unit tests ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stage3_returns_non_empty_answer():
    client = _fake_client()
    vdb_id = f"sim_events_{SCENARIO_ID}"
    await client.ensure_vector_db(vdb_id)
    await client.ingest_documents(
        [{"id": "doc-1", "content": "Substation A tripped offline."}],
        vdb_id,
    )

    subgraph = AffectedSubgraph(
        event_id="scenario:s",
        scenario_id=SCENARIO_ID,
        affected_entity_ids=[ENTITY_A],
    )
    solver_result = StubSolver().solve(subgraph, {})

    answer, trace = await run_stage3(
        question=QUESTION,
        subgraph=subgraph,
        solver_result=solver_result,
        llm_client=client,
    )

    assert isinstance(answer, str)
    assert len(answer) > 0
    assert trace == []  # no tools called when pool/live_state are not supplied


@pytest.mark.asyncio
async def test_stage3_fallback_when_no_vector_context():
    """Stage 3 must not crash when the vector DB has no matching chunks."""
    client = _fake_client()
    # Do NOT ingest any documents; vector_search returns []

    subgraph = AffectedSubgraph(
        event_id="s", scenario_id=SCENARIO_ID, affected_entity_ids=[ENTITY_A]
    )
    solver_result = StubSolver().solve(subgraph, {})

    answer, trace = await run_stage3(
        question=QUESTION,
        subgraph=subgraph,
        solver_result=solver_result,
        llm_client=client,
    )

    assert isinstance(answer, str)
    assert len(answer) > 0
    assert trace == []


@pytest.mark.asyncio
async def test_pipeline_react_calls_subgraph_and_solver():
    """ReAct pipeline: agent calls get_affected_subgraph + solve_impact before answering.

    This is the canonical agentic workflow: the LLM decides to investigate the
    graph and run the solver rather than answering immediately from prior context.
    """
    driver = _standard_neo4j_driver()
    conn = _conn()
    conn.fetch.side_effect = _standard_pool_side_effect()
    pool = _pool(conn)

    client = FakeLLMClient(settings=_settings(), response_sequence=_react_sequence())

    request = QueryRequest(question=QUESTION, scenario_id=SCENARIO_ID)
    response = await run_pipeline(request, driver, pool, client, StubSolver())

    assert isinstance(response.answer, str)
    assert len(response.answer) > 0

    # The agent called get_affected_subgraph → affected_entities is populated.
    assert set(response.affected_entities) == set(ALL_ENTITIES)

    # The agent called solve_impact → solver output is populated.
    assert response.solver.affected_count == 3
    assert response.solver.max_chain_length == 2
    assert response.solver.impact_score > 0
    assert len(response.solver.response_options) > 0

    # Tool call trace shows the 2 tool calls the agent made.
    tool_names = [r.tool_name for r in response.tool_call_trace]
    assert "get_affected_subgraph" in tool_names
    assert "solve_impact" in tool_names


# ── Pipeline orchestrator unit tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_run_returns_structured_response():
    driver = _standard_neo4j_driver()
    conn = _conn()
    conn.fetch.side_effect = _standard_pool_side_effect()
    pool = _pool(conn)

    # Use a full ReAct sequence so the agent actually calls the tools.
    client = FakeLLMClient(settings=_settings(), response_sequence=_react_sequence())

    request = QueryRequest(question=QUESTION, scenario_id=SCENARIO_ID)
    response = await run_pipeline(request, driver, pool, client, StubSolver())

    assert response.question == QUESTION
    assert response.scenario_id == SCENARIO_ID
    assert set(response.affected_entities) == set(ALL_ENTITIES)
    assert response.solver.affected_count == 3
    assert response.solver.max_chain_length == 2
    assert response.solver.impact_score > 0
    assert len(response.answer) > 0
    assert len(response.solver.response_options) > 0
    assert isinstance(response.tool_call_trace, list)
    assert len(response.tool_call_trace) == 2  # subgraph + solver


@pytest.mark.asyncio
async def test_pipeline_no_events_returns_empty_affected_set():
    """Pipeline must succeed gracefully when no events are injected.

    The agent answers immediately (no tool calls) because the scenario is empty.
    """
    driver = _neo4j_driver([])  # no entities in Neo4j
    conn = _conn()
    pool = _pool(conn)

    # No response_sequence → agent answers directly without calling tools.
    client = _fake_client()
    request = QueryRequest(question=QUESTION, scenario_id="empty_scenario")
    response = await run_pipeline(request, driver, pool, client, StubSolver())

    assert response.affected_entities == []
    assert response.solver.affected_count == 0
    assert isinstance(response.answer, str)
    assert response.tool_call_trace == []  # agent made no tool calls


# ── POST /query end-to-end HTTP tests ─────────────────────────────────────────


@pytest.fixture()
def _query_app_overrides():
    """Set up dependency overrides for POST /query tests; tear down after."""
    driver = _standard_neo4j_driver()
    conn = _conn()
    conn.fetch.side_effect = _standard_pool_side_effect()
    pool = _pool(conn)

    # Use the full ReAct sequence so the agent calls get_affected_subgraph +
    # solve_impact before answering — this exercises the complete agentic path.
    client = FakeLLMClient(settings=_settings(), response_sequence=_react_sequence())

    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_neo4j_driver] = lambda: driver
    app.dependency_overrides[get_llm_client] = lambda: client
    app.dependency_overrides[get_solver] = lambda: StubSolver()

    yield conn, client

    app.dependency_overrides.pop(get_pool, None)
    app.dependency_overrides.pop(get_neo4j_driver, None)
    app.dependency_overrides.pop(get_llm_client, None)
    app.dependency_overrides.pop(get_solver, None)


@pytest.mark.asyncio
async def test_post_query_end_to_end(_query_app_overrides):
    """
    Full HTTP round-trip: POST /query → pipeline → structured JSON response.

    Verifies:
      - HTTP 200 and correct response shape
      - Stage-1 affected entities match mocked AGE traversal output
      - Stage-2 solver ran (affected_count and chain length present)
      - Stage-3 synthesis produced a non-empty answer string
    """
    conn, client = _query_app_overrides

    # Pre-ingest an event description into the fake vector store so Stage 3
    # has context to retrieve.
    vdb_id = f"sim_events_{SCENARIO_ID}"
    await client.ensure_vector_db(vdb_id)
    await client.ingest_documents(
        [
            {
                "id": "evt-e2e-1",
                "content": "Substation A tripped offline; cascading fault reaches B and C.",
            }
        ],
        vdb_id,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        response = await http.post(
            "/query",
            json={"question": QUESTION, "scenario_id": SCENARIO_ID},
        )

    assert response.status_code == 200, response.text
    body = response.json()

    # Shape
    assert "answer" in body
    assert "affected_entities" in body
    assert "solver" in body
    assert "question" in body
    assert "scenario_id" in body
    assert "tool_call_trace" in body
    assert isinstance(body["tool_call_trace"], list)

    # Agent called get_affected_subgraph → entities populated.
    assert set(body["affected_entities"]) == {ENTITY_A, ENTITY_B, ENTITY_C}

    # Agent called solve_impact → solver output populated.
    assert body["solver"]["affected_count"] == 3
    assert body["solver"]["max_chain_length"] == 2
    assert body["solver"]["impact_score"] > 0
    assert isinstance(body["solver"]["response_options"], list)
    assert len(body["solver"]["response_options"]) > 0

    # Final synthesis answer is non-empty.
    assert isinstance(body["answer"], str)
    assert len(body["answer"]) > 0

    # Tool call trace shows both agent tool calls.
    assert len(body["tool_call_trace"]) == 2
    trace_names = [r["tool_name"] for r in body["tool_call_trace"]]
    assert "get_affected_subgraph" in trace_names
    assert "solve_impact" in trace_names

    # Passthrough fields
    assert body["question"] == QUESTION
    assert body["scenario_id"] == SCENARIO_ID


@pytest.mark.asyncio
async def test_post_query_missing_required_fields():
    """POST /query with a missing field should return 422 Unprocessable Entity."""
    # Deps must be present so FastAPI can reach Pydantic validation;
    # the handler itself never runs when the body fails validation.
    conn = _conn()
    pool = _pool(conn)
    driver = _neo4j_driver([])
    client = _fake_client()
    app.dependency_overrides[get_pool] = lambda: pool
    app.dependency_overrides[get_neo4j_driver] = lambda: driver
    app.dependency_overrides[get_llm_client] = lambda: client
    app.dependency_overrides[get_solver] = lambda: StubSolver()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as http:
            response = await http.post("/query", json={"question": QUESTION})
            assert response.status_code == 422  # scenario_id missing

            response2 = await http.post("/query", json={"scenario_id": SCENARIO_ID})
            assert response2.status_code == 422  # question missing
    finally:
        app.dependency_overrides.pop(get_pool, None)
        app.dependency_overrides.pop(get_neo4j_driver, None)
        app.dependency_overrides.pop(get_llm_client, None)
        app.dependency_overrides.pop(get_solver, None)
