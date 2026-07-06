"""Generic Entity node and dependency edge operations via Neo4j (Cypher).

Domain rule: NO domain-specific labels or property names here.
  - Nodes are always labelled ``Entity``; their domain type is a *property*.
  - Dependency semantics live in the edge type string (e.g. ``DEPENDS_ON``,
    ``FEEDS``, ``SUPPLIES``); callers choose the string, this module does not.
"""
from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncDriver

from src.graph.cypher import neo4j_session

logger = logging.getLogger(__name__)

EDGE_DEPENDS_ON = "DEPENDS_ON"
EDGE_FEEDS = "FEEDS"


# ---------------------------------------------------------------------------
# Entity node operations
# ---------------------------------------------------------------------------


async def create_entity_node(
    driver: AsyncDriver,
    entity_id: str,
    entity_type: str,
    attributes: dict[str, Any] | None = None,
) -> None:
    """MERGE an Entity node into the graph.

    Idempotent: safe to call for an entity that already exists.
    Existing node properties are updated with the supplied values.
    """
    query = (
        "MERGE (n:Entity {id: $id}) "
        "SET n.type = $type "
        "RETURN id(n)"
    )
    async with neo4j_session(driver) as session:
        await session.run(query, id=entity_id, type=entity_type)
    logger.debug("Entity node upserted: id=%s type=%s", entity_id, entity_type)


async def delete_entity_node(
    driver: AsyncDriver,
    entity_id: str,
) -> None:
    """Remove an Entity node and all its edges."""
    query = "MATCH (n:Entity {id: $id}) DETACH DELETE n"
    async with neo4j_session(driver) as session:
        await session.run(query, id=entity_id)
    logger.debug("Entity node deleted: id=%s", entity_id)


async def get_entity_node(
    driver: AsyncDriver,
    entity_id: str,
) -> dict[str, Any] | None:
    """Return the properties of an Entity node, or None if not found."""
    query = "MATCH (n:Entity {id: $id}) RETURN properties(n) AS props"
    async with neo4j_session(driver) as session:
        result = await session.run(query, id=entity_id)
        record = await result.single()
    if record is None:
        return None
    return dict(record["props"])


# ---------------------------------------------------------------------------
# Dependency edge operations
# ---------------------------------------------------------------------------


async def create_dependency_edge(
    driver: AsyncDriver,
    from_id: str,
    to_id: str,
    edge_type: str = EDGE_DEPENDS_ON,
) -> None:
    """Create a directed dependency edge from *from_id* to *to_id*.

    If the edge already exists, a duplicate is created — call
    ``delete_dependency_edge`` first if idempotency is required.
    """
    query = (
        f"MATCH (a:Entity {{id: $from_id}}), (b:Entity {{id: $to_id}}) "
        f"CREATE (a)-[:{edge_type}]->(b) "
        "RETURN id(a)"
    )
    async with neo4j_session(driver) as session:
        await session.run(query, from_id=from_id, to_id=to_id)
    logger.debug("Dependency edge created: %s -[%s]-> %s", from_id, edge_type, to_id)


async def get_dependent_entities(
    driver: AsyncDriver,
    entity_id: str,
    edge_type: str | None = None,
) -> list[str]:
    """Return the IDs of entities that *entity_id* depends on."""
    if edge_type:
        query = (
            f"MATCH (a:Entity {{id: $id}})-[:{edge_type}]->(b:Entity) "
            "RETURN b.id AS dep_id"
        )
    else:
        query = (
            "MATCH (a:Entity {id: $id})-[]->(b:Entity) "
            "RETURN b.id AS dep_id"
        )
    async with neo4j_session(driver) as session:
        result = await session.run(query, id=entity_id)
        records = await result.data()
    return [r["dep_id"] for r in records if r.get("dep_id") is not None]
