"""Stage 1 — Structural traversal (deterministic, no LLM).

Queries Neo4j directly to collect:
  1. Every Entity node reachable via AFFECTED_BY edges from any
     SimulationEvent in the given scenario.
  2. Every dependency edge between those entities within the subgraph.

Output is an AffectedSubgraph ready for Stage 2.

Rules:
  - No LLM calls here.
  - Read-only — never mutates the graph.
"""
from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncDriver, AsyncSession

from src.core.solver import AffectedSubgraph
from src.graph.cypher import neo4j_session

logger = logging.getLogger(__name__)


async def run_stage1(
    scenario_id: str,
    driver: AsyncDriver,
) -> AffectedSubgraph:
    """Collect the full affected subgraph for every event in *scenario_id*.

    Step A: MATCH all Entity nodes connected via AFFECTED_BY to any
            SimulationEvent whose scenario_id = *scenario_id*.
    Step B: MATCH all dependency edges between those entities (sub-DAG).

    Returns an empty subgraph if no events have been injected yet.
    """
    async with neo4j_session(driver) as session:
        entity_ids = await _get_affected_entities(session, scenario_id)

        if not entity_ids:
            logger.info(
                "Stage 1: no affected entities found for scenario=%s", scenario_id
            )
            return AffectedSubgraph(
                event_id=f"scenario:{scenario_id}",
                scenario_id=scenario_id,
                affected_entity_ids=[],
            )

        edges = await _get_dependency_edges(session, entity_ids)
        entity_attrs = await _get_entity_attributes(session, entity_ids)

    logger.info(
        "Stage 1 complete: scenario=%s entities=%d edges=%d",
        scenario_id,
        len(entity_ids),
        len(edges),
    )
    return AffectedSubgraph(
        event_id=f"scenario:{scenario_id}",
        scenario_id=scenario_id,
        affected_entity_ids=entity_ids,
        dependency_edges=edges,
        entity_attributes=entity_attrs,
    )


async def _get_affected_entities(
    session: AsyncSession,
    scenario_id: str,
) -> list[str]:
    """MATCH entities connected via AFFECTED_BY to any event in the scenario."""
    query = (
        "MATCH (n:Entity)-[:AFFECTED_BY]->(e:SimulationEvent {scenario_id: $sid}) "
        "RETURN DISTINCT n.id AS entity_id"
    )
    result = await session.run(query, sid=scenario_id)
    records = await result.data()
    return [r["entity_id"] for r in records if r.get("entity_id") is not None]


async def _get_entity_attributes(
    session: AsyncSession,
    entity_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return all node properties for each entity in *entity_ids*."""
    query = (
        "MATCH (n:Entity) "
        "WHERE n.id IN $ids "
        "RETURN properties(n) AS props"
    )
    result = await session.run(query, ids=entity_ids)
    records = await result.data()
    out: dict[str, dict[str, Any]] = {}
    for r in records:
        props = r.get("props")
        if isinstance(props, dict):
            eid = props.get("id")
            if eid:
                out[str(eid)] = props
    return out


async def _get_dependency_edges(
    session: AsyncSession,
    entity_ids: list[str],
) -> list[tuple[str, str, str]]:
    """MATCH dependency edges between the given entities (within subgraph)."""
    query = (
        "MATCH (a:Entity)-[r]->(b:Entity) "
        "WHERE a.id IN $ids AND b.id IN $ids "
        "RETURN a.id AS from_id, type(r) AS edge_type, b.id AS to_id"
    )
    result = await session.run(query, ids=entity_ids)
    records = await result.data()
    edges: list[tuple[str, str, str]] = []
    for r in records:
        from_id = r.get("from_id")
        edge_type = r.get("edge_type")
        to_id = r.get("to_id")
        if from_id and edge_type and to_id:
            edges.append((str(from_id), str(to_id), str(edge_type)))
    return edges
