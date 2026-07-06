"""Simulation event management — graph overlay + vector embedding.

A SimulationEvent is injected as an *overlay* on top of the base graph:
  - A SimulationEvent node is created in Neo4j.
  - AFFECTED_BY edges connect each perturbed Entity to the event node.
  - The event description is ingested into a vector DB for RAG.

Crucially:
  - Base Entity nodes are NEVER modified.
  - Injecting an event is purely additive; removing it restores the original.
  - Multiple concurrent events (different scenario_id values) are fully
    independent — removing one leaves the others intact.

Vector DB naming: each scenario gets its own vector DB:
    f"sim_events_{scenario_id}"
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from neo4j import AsyncDriver

from src.graph.cypher import neo4j_session
from src.llm.base import LLMClientBase

logger = logging.getLogger(__name__)

EDGE_AFFECTED_BY = "AFFECTED_BY"


def _vector_db_id(scenario_id: str) -> str:
    return f"sim_events_{scenario_id}"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SimulationEvent:
    """An overlay event that perturbs one or more entities without mutating
    the live store.

    Attributes:
        id:                   Unique event identifier.  Also used as the
                              document_id in the vector store.
        scenario_id:          Groups events; multiple events with the same
                              scenario_id form a compound what-if scenario.
        description:          Human-readable text describing the perturbation.
                              Embedded into the vector DB for RAG retrieval.
        affected_entity_ids:  Graph-node IDs of entities this event perturbs.
                              AFFECTED_BY edges are created for each one.
        attributes:           Arbitrary metadata (severity, category, etc.).
        created_at:           Timestamp of injection (set automatically).
    """

    id: str
    scenario_id: str
    description: str
    affected_entity_ids: list[str]
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


# ---------------------------------------------------------------------------
# Inject
# ---------------------------------------------------------------------------


async def inject_event(
    event: SimulationEvent,
    driver: AsyncDriver,
    llm_client: LLMClientBase,
) -> None:
    """Inject a simulation event as an overlay.

    Steps (all additive — does NOT touch live-store or base entity nodes):
      1. Create a SimulationEvent node in Neo4j.
      2. Create an AFFECTED_BY edge from each affected Entity to the event.
      3. Ingest the event description into the scenario's vector DB.
    """
    vdb = _vector_db_id(event.scenario_id)

    async with neo4j_session(driver) as session:
        await _create_event_node(session, event)
        for entity_id in event.affected_entity_ids:
            await _create_affected_by_edge(session, entity_id, event.id)

    await llm_client.ensure_vector_db(vdb)
    await llm_client.ingest_documents(
        documents=[
            {
                "id": event.id,
                "content": event.description,
                "metadata": {
                    "scenario_id": event.scenario_id,
                    "event_id": event.id,
                    "affected_entity_ids": event.affected_entity_ids,
                    **event.attributes,
                },
            }
        ],
        vector_db_id=vdb,
    )

    logger.info(
        "Injected event: id=%s scenario=%s affected=%s",
        event.id,
        event.scenario_id,
        event.affected_entity_ids,
    )


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


async def remove_event(
    event_id: str,
    driver: AsyncDriver,
) -> None:
    """Remove a single simulation event from the graph.

    DETACH DELETE removes the SimulationEvent node and all its AFFECTED_BY
    edges in one operation.  Base Entity nodes are untouched.
    """
    query = "MATCH (e:SimulationEvent {id: $id}) DETACH DELETE e"
    async with neo4j_session(driver) as session:
        await session.run(query, id=event_id)
    logger.info("Removed event from graph: id=%s", event_id)


async def remove_scenario(
    scenario_id: str,
    driver: AsyncDriver,
    llm_client: LLMClientBase,
) -> None:
    """Remove ALL events belonging to *scenario_id* — graph and vector store."""
    query = "MATCH (e:SimulationEvent {scenario_id: $sid}) DETACH DELETE e"
    async with neo4j_session(driver) as session:
        await session.run(query, sid=scenario_id)

    vdb = _vector_db_id(scenario_id)
    await llm_client.unregister_vector_db(vdb)
    logger.info("Removed scenario: id=%s vector_db=%s", scenario_id, vdb)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


async def get_affected_entities(
    driver: AsyncDriver,
    event_id: str,
) -> list[str]:
    """Return entity IDs connected to *event_id* via AFFECTED_BY edges."""
    query = (
        "MATCH (n:Entity)-[:AFFECTED_BY]->(e:SimulationEvent {id: $id}) "
        "RETURN n.id AS entity_id"
    )
    async with neo4j_session(driver) as session:
        result = await session.run(query, id=event_id)
        records = await result.data()
    return [r["entity_id"] for r in records if r.get("entity_id") is not None]


async def get_scenario_events(
    driver: AsyncDriver,
    scenario_id: str,
) -> list[str]:
    """Return event IDs belonging to *scenario_id*."""
    query = (
        "MATCH (e:SimulationEvent {scenario_id: $sid}) "
        "RETURN e.id AS event_id"
    )
    async with neo4j_session(driver) as session:
        result = await session.run(query, sid=scenario_id)
        records = await result.data()
    return [r["event_id"] for r in records if r.get("event_id") is not None]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _create_event_node(session: Any, event: SimulationEvent) -> None:
    query = (
        "CREATE (e:SimulationEvent {"
        "id: $id, "
        "scenario_id: $scenario_id, "
        "description: $description"
        "}) RETURN id(e)"
    )
    await session.run(
        query,
        id=event.id,
        scenario_id=event.scenario_id,
        description=event.description,
    )


async def _create_affected_by_edge(
    session: Any,
    entity_id: str,
    event_id: str,
) -> None:
    query = (
        "MATCH (n:Entity {id: $entity_id}), "
        "(e:SimulationEvent {id: $event_id}) "
        f"CREATE (n)-[:{EDGE_AFFECTED_BY}]->(e) "
        "RETURN id(e)"
    )
    await session.run(query, entity_id=entity_id, event_id=event_id)
