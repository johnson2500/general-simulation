"""Seed the Neo4j graph with demo aircraft entities and a simulation event.

Simulates a set of OpenSky aircraft transiting European airspace, wires
dependency edges (shared routes), then injects a UK airspace closure event
that affects a subset of them.

Run via oc exec:
    oc exec -n general-sim deployment/general-sim-api -- \
        python /app/scripts/seed_demo.py
"""
from __future__ import annotations

import asyncio
import logging
import os

from neo4j import AsyncGraphDatabase

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]

# ---------------------------------------------------------------------------
# Demo aircraft — realistic OpenSky entity IDs (icao24 codes)
# ---------------------------------------------------------------------------

AIRCRAFT = [
    {"id": "opensky-407290", "callsign": "BAW442",  "origin": "United Kingdom", "route": "LHR-JFK", "status": "airborne"},
    {"id": "opensky-3c6444", "callsign": "DLH456",  "origin": "Germany",        "route": "FRA-ORD", "status": "airborne"},
    {"id": "opensky-484161", "callsign": "AFR832",  "origin": "France",          "route": "CDG-LAX", "status": "airborne"},
    {"id": "opensky-4ca87e", "callsign": "EIN204",  "origin": "Ireland",         "route": "DUB-BOS", "status": "airborne"},
    {"id": "opensky-3c4b58", "callsign": "EWG1234", "origin": "Germany",        "route": "MUC-LHR", "status": "airborne"},
    {"id": "opensky-471f52", "callsign": "VIR025",  "origin": "United Kingdom", "route": "LHR-MIA", "status": "airborne"},
    {"id": "opensky-40617d", "callsign": "EZY8742", "origin": "United Kingdom", "route": "LGW-FCO", "status": "airborne"},
    {"id": "opensky-3c0c7e", "callsign": "TUI6321", "origin": "Germany",        "route": "STN-PMI", "status": "on_ground"},
]

UK_AIRSPACE_AFFECTED = [
    "opensky-407290",  # BAW442  — departing LHR
    "opensky-4ca87e",  # EIN204  — transiting UK to North Atlantic
    "opensky-3c4b58",  # EWG1234 — arriving LHR
    "opensky-471f52",  # VIR025  — departing LHR
    "opensky-40617d",  # EZY8742 — departing LGW
]

DEPENDENCIES = [
    ("opensky-407290", "opensky-471f52"),  # both on LHR North Atlantic slots
    ("opensky-4ca87e", "opensky-407290"),  # DUB-BOS feeds same NATS track
    ("opensky-3c4b58", "opensky-3c6444"),  # MUC-LHR feeds FRA-ORD connection
    ("opensky-40617d", "opensky-484161"),  # LGW-FCO shares Med corridor with CDG-LAX
]

SCENARIO_ID = "opensky-uk-closure-001"
EVENT_ID = "evt-uk-airspace-closure-20260630"
EVENT_DESCRIPTION = (
    "UK airspace has been closed to all civilian traffic effective 13:00 UTC "
    "on 30 June 2026 due to a critical GPS/navigation system failure affecting "
    "NATS (National Air Traffic Services). All aircraft currently airborne in "
    "UK airspace (London FIR and Scottish FIR) must divert immediately. "
    "Inbound flights to LHR, LGW, MAN, and EDI are suspended. "
    "Transatlantic traffic on NATS tracks is rerouted via oceanic contingency "
    "tracks further north or through Shanwick/Gander delegation."
)


async def main() -> None:
    logger.info("Connecting to Neo4j at %s …", NEO4J_URI)
    driver = AsyncGraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        async with driver.session(database="neo4j") as session:
            # 1. Upsert Entity nodes
            logger.info("Creating %d aircraft Entity nodes …", len(AIRCRAFT))
            for ac in AIRCRAFT:
                await session.run(
                    "MERGE (n:Entity {id: $id}) "
                    "SET n.type = $type, n.callsign = $callsign, "
                    "    n.origin = $origin, n.route = $route",
                    id=ac["id"],
                    type="moving_entity",
                    callsign=ac["callsign"],
                    origin=ac["origin"],
                    route=ac["route"],
                )
                logger.info("  ✓ %s (%s) — %s", ac["id"], ac["callsign"], ac["route"])

            # 2. Create dependency edges
            logger.info("Wiring %d dependency edges …", len(DEPENDENCIES))
            for from_id, to_id in DEPENDENCIES:
                await session.run(
                    "MATCH (a:Entity {id: $from_id}), (b:Entity {id: $to_id}) "
                    "MERGE (a)-[:DEPENDS_ON]->(b)",
                    from_id=from_id,
                    to_id=to_id,
                )
                logger.info("  ✓ %s → %s", from_id, to_id)

            # 3. Create SimulationEvent node
            logger.info("Injecting SimulationEvent: %s …", EVENT_ID)
            await session.run(
                "MERGE (e:SimulationEvent {id: $id}) "
                "SET e.scenario_id = $scenario_id, e.description = $description",
                id=EVENT_ID,
                scenario_id=SCENARIO_ID,
                description=EVENT_DESCRIPTION[:200],
            )

            # 4. Wire AFFECTED_BY edges
            logger.info("Wiring AFFECTED_BY edges for %d aircraft …", len(UK_AIRSPACE_AFFECTED))
            for entity_id in UK_AIRSPACE_AFFECTED:
                await session.run(
                    "MATCH (n:Entity {id: $entity_id}), (e:SimulationEvent {id: $event_id}) "
                    "MERGE (n)-[:AFFECTED_BY]->(e)",
                    entity_id=entity_id,
                    event_id=EVENT_ID,
                )
                logger.info("  ✓ %s AFFECTED_BY %s", entity_id, EVENT_ID)

            # 5. Verify
            result = await session.run(
                "MATCH (n:Entity)-[:AFFECTED_BY]->(e:SimulationEvent {scenario_id: $sid}) "
                "RETURN n.id AS entity_id",
                sid=SCENARIO_ID,
            )
            records = await result.data()
            logger.info(
                "\nSeed complete. %d affected entities in scenario '%s'.",
                len(records),
                SCENARIO_ID,
            )
            logger.info("\nRun this query to test:")
            logger.info('  scenario_id: "%s"', SCENARIO_ID)
            logger.info(
                '  question:    "UK airspace is closed due to a NATS GPS failure. '
                'Which aircraft are affected and what diversions should be issued?"'
            )
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
