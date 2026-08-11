"""Neo4j dependency wiring + LA port-closure scenario for shipping.

Adapters only fill the live store.  Call ``bootstrap_shipping_graph`` after
ingestion (or via ``scripts/seed_shipping.py``) to MERGE dependency edges and
inject the simulation event overlay.
"""
from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncDriver

from src.graph.nodes import EDGE_CARRIES, EDGE_DEPENDS_ON, EDGE_FEEDS
from src.graph.spatial_overlay import format_bbox

logger = logging.getLogger(__name__)

# Tight envelope around POLA / POLB — vessels at anchor / berth / approach
# fall inside so spatial overlay marks them AFFECTED_BY.
LA_PORT_BBOX = (-118.35, 33.70, -118.15, 33.85)

SCENARIO_ID = "shipping-la-closure-001"
EVENT_ID = "evt-la-port-strike-20260806"
EVENT_DESCRIPTION = (
    "Port of Los Angeles closed due to a labor strike effective 06:00 UTC "
    "on 6 August 2026. Inbound container traffic to POLA is suspended. "
    "Port of Long Beach is constrained by shared pilotage and labor spillover. "
    "Asia–US West Coast vessels are diverting to Oakland and Seattle or "
    "holding at anchorage pending berth availability."
)

# (from_id, to_id) — A is blocked / constrained if B is disrupted.
DEPENDENCIES: list[tuple[str, str]] = [
    ("vessel-ever-green-01", "port-us-lax"),
    ("vessel-cosco-pacific-07", "port-us-lax"),
    ("vessel-maersk-horizon-03", "port-us-lgb"),
    ("vessel-yangming-star-02", "port-us-lax"),
    ("port-us-lgb", "port-us-lax"),
    ("vessel-hapag-transpac-12", "port-us-oak"),
]

# Origin port feeds vessel voyage.
FEEDS: list[tuple[str, str]] = [
    ("port-cn-sha", "vessel-ever-green-01"),
    ("port-cn-yantian", "vessel-cosco-pacific-07"),
    ("port-cn-sha", "vessel-yangming-star-02"),
    ("port-cn-sha", "vessel-maersk-horizon-03"),
    ("port-cn-sha", "vessel-hapag-transpac-12"),
    ("port-cn-yantian", "vessel-one-cascade-09"),
]

# (carrier_id, cargo_id)
CARRIES: list[tuple[str, str]] = [
    ("vessel-ever-green-01", "cargo-ever-green-01-1"),
    ("vessel-ever-green-01", "cargo-ever-green-01-2"),
    ("vessel-cosco-pacific-07", "cargo-cosco-pacific-07-1"),
    ("vessel-cosco-pacific-07", "cargo-cosco-pacific-07-2"),
    ("vessel-maersk-horizon-03", "cargo-maersk-horizon-03-1"),
    ("vessel-maersk-horizon-03", "cargo-maersk-horizon-03-2"),
    ("vessel-hapag-transpac-12", "cargo-hapag-transpac-12-1"),
    ("vessel-one-cascade-09", "cargo-one-cascade-09-1"),
    ("vessel-yangming-star-02", "cargo-yangming-star-02-1"),
    ("vessel-yangming-star-02", "cargo-yangming-star-02-2"),
]


async def bootstrap_shipping_graph(driver: AsyncDriver) -> dict[str, Any]:
    """MERGE shipping dependency edges and inject the LA closure event.

    Entity nodes for vessels/ports/cargo should already exist (ingestion
    upserts them when a Neo4j driver is passed).  This function MERGEs nodes
    defensively so bootstrap remains safe if run alone after a Postgres-only
    ingest.
    """
    bbox = format_bbox(LA_PORT_BBOX)
    entity_ids = {
        *[a for a, _ in DEPENDENCIES],
        *[b for _, b in DEPENDENCIES],
        *[a for a, _ in FEEDS],
        *[b for _, b in FEEDS],
        *[a for a, _ in CARRIES],
        *[b for _, b in CARRIES],
    }

    async with driver.session(database="neo4j") as session:
        for entity_id in sorted(entity_ids):
            await session.run(
                "MERGE (n:Entity {id: $id})",
                id=entity_id,
            )

        for from_id, to_id in DEPENDENCIES:
            await session.run(
                "MATCH (a:Entity {id: $from_id}), (b:Entity {id: $to_id}) "
                f"MERGE (a)-[:{EDGE_DEPENDS_ON}]->(b)",
                from_id=from_id,
                to_id=to_id,
            )
            logger.info("  ✓ %s -[%s]-> %s", from_id, EDGE_DEPENDS_ON, to_id)

        for from_id, to_id in FEEDS:
            await session.run(
                "MATCH (a:Entity {id: $from_id}), (b:Entity {id: $to_id}) "
                f"MERGE (a)-[:{EDGE_FEEDS}]->(b)",
                from_id=from_id,
                to_id=to_id,
            )
            logger.info("  ✓ %s -[%s]-> %s", from_id, EDGE_FEEDS, to_id)

        for carrier_id, cargo_id in CARRIES:
            await session.run(
                "MATCH (carrier:Entity {id: $carrier_id}), "
                "      (cargo:Entity {id: $cargo_id}) "
                f"MERGE (carrier)-[:{EDGE_CARRIES}]->(cargo)",
                carrier_id=carrier_id,
                cargo_id=cargo_id,
            )
            logger.info(
                "  ✓ %s -[%s]-> %s", carrier_id, EDGE_CARRIES, cargo_id
            )

        await session.run(
            "MERGE (e:SimulationEvent {id: $id}) "
            "SET e.scenario_id = $scenario_id, "
            "    e.description = $description, "
            "    e.affect_bbox = $bbox",
            id=EVENT_ID,
            scenario_id=SCENARIO_ID,
            description=EVENT_DESCRIPTION[:500],
            bbox=bbox,
        )
        logger.info("  ✓ event %s affect_bbox=%s", EVENT_ID, bbox)

    return {
        "scenario_id": SCENARIO_ID,
        "event_id": EVENT_ID,
        "affect_bbox": bbox,
        "depends_on": len(DEPENDENCIES),
        "feeds": len(FEEDS),
        "carries": len(CARRIES),
    }
