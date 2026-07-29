"""Seed Neo4j + Postgres with demo aircraft, cargo, and a UK airspace closure.

Simulates OpenSky aircraft in European airspace, wires dependency and cargo
edges, injects a simulation event overlay, and upserts matching PostGIS points
so the Supply Chain Map page has geometries to display.

Run from the repo root:
    uv run python scripts/seed_demo.py

    Or via oc exec:
    oc exec -n general-sim deployment/general-sim-api -- \
        python /app/scripts/seed_demo.py
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from neo4j import AsyncGraphDatabase

from src.core.config import Settings
from src.core.db import create_pool
from src.core.ingestion import CanonicalEntity
from src.graph.nodes import EDGE_CARRIES
from src.graph.spatial_overlay import UK_AIRSPACE_BBOX, format_bbox
from src.ingestion.runner import _insert_state, _upsert_entity

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Demo aircraft — realistic OpenSky entity IDs (icao24 codes)
# lon/lat are demo WGS84 positions near UK/EU routes (not live ADS-B).
# revenue_usd is synthetic passenger/ops revenue for cost-of-impact demos.
# ---------------------------------------------------------------------------

AIRCRAFT = [
    {
        "id": "opensky-407290",
        "callsign": "BAW442",
        "origin": "United Kingdom",
        "route": "LHR-JFK",
        "status": "airborne",
        "lon": -0.55,
        "lat": 51.48,
        "revenue_usd": 620_000.0,
    },
    {
        "id": "opensky-3c6444",
        "callsign": "DLH456",
        "origin": "Germany",
        "route": "FRA-ORD",
        "status": "airborne",
        "lon": 8.57,
        "lat": 50.05,
        "revenue_usd": 540_000.0,
    },
    {
        "id": "opensky-484161",
        "callsign": "AFR832",
        "origin": "France",
        "route": "CDG-LAX",
        "status": "airborne",
        "lon": 2.55,
        "lat": 49.01,
        "revenue_usd": 710_000.0,
    },
    {
        "id": "opensky-4ca87e",
        "callsign": "EIN204",
        "origin": "Ireland",
        "route": "DUB-BOS",
        "status": "airborne",
        "lon": -6.25,
        "lat": 53.42,
        "revenue_usd": 380_000.0,
    },
    {
        "id": "opensky-3c4b58",
        "callsign": "EWG1234",
        "origin": "Germany",
        "route": "MUC-LHR",
        "status": "airborne",
        "lon": -0.30,
        "lat": 51.35,
        "revenue_usd": 95_000.0,
    },
    {
        "id": "opensky-471f52",
        "callsign": "VIR025",
        "origin": "United Kingdom",
        "route": "LHR-MIA",
        "status": "airborne",
        "lon": -0.70,
        "lat": 51.55,
        "revenue_usd": 580_000.0,
    },
    {
        "id": "opensky-40617d",
        "callsign": "EZY8742",
        "origin": "United Kingdom",
        "route": "LGW-FCO",
        "status": "airborne",
        "lon": -0.18,
        "lat": 51.15,
        "revenue_usd": 72_000.0,
    },
    {
        "id": "opensky-3c0c7e",
        "callsign": "TUI6321",
        "origin": "Germany",
        "route": "STN-PMI",
        "status": "on_ground",
        "lon": 0.23,
        "lat": 51.88,
        "revenue_usd": 48_000.0,
    },
]

# Cargo aboard airborne flights — no geometry (map stays flight-only).
# value_usd = quantity * unit_price_usd (precomputed for the solver).
CARGO: list[dict] = [
    {
        "id": "cargo-opensky-407290-1",
        "carrier_id": "opensky-407290",
        "commodity": "pharmaceuticals",
        "quantity": 12,
        "unit_price_usd": 8500.0,
    },
    {
        "id": "cargo-opensky-407290-2",
        "carrier_id": "opensky-407290",
        "commodity": "electronics",
        "quantity": 40,
        "unit_price_usd": 1200.0,
    },
    {
        "id": "cargo-opensky-471f52-1",
        "carrier_id": "opensky-471f52",
        "commodity": "aerospace_parts",
        "quantity": 6,
        "unit_price_usd": 22000.0,
    },
    {
        "id": "cargo-opensky-471f52-2",
        "carrier_id": "opensky-471f52",
        "commodity": "perishables",
        "quantity": 18,
        "unit_price_usd": 950.0,
    },
    {
        "id": "cargo-opensky-4ca87e-1",
        "carrier_id": "opensky-4ca87e",
        "commodity": "medical_devices",
        "quantity": 8,
        "unit_price_usd": 15000.0,
    },
    {
        "id": "cargo-opensky-4ca87e-2",
        "carrier_id": "opensky-4ca87e",
        "commodity": "apparel",
        "quantity": 120,
        "unit_price_usd": 85.0,
    },
    {
        "id": "cargo-opensky-3c4b58-1",
        "carrier_id": "opensky-3c4b58",
        "commodity": "automotive_parts",
        "quantity": 25,
        "unit_price_usd": 410.0,
    },
    {
        "id": "cargo-opensky-3c4b58-2",
        "carrier_id": "opensky-3c4b58",
        "commodity": "machinery",
        "quantity": 3,
        "unit_price_usd": 18000.0,
    },
    {
        "id": "cargo-opensky-40617d-1",
        "carrier_id": "opensky-40617d",
        "commodity": "wine",
        "quantity": 50,
        "unit_price_usd": 160.0,
    },
    {
        "id": "cargo-opensky-40617d-2",
        "carrier_id": "opensky-40617d",
        "commodity": "olive_oil",
        "quantity": 30,
        "unit_price_usd": 95.0,
    },
    {
        "id": "cargo-opensky-484161-1",
        "carrier_id": "opensky-484161",
        "commodity": "luxury_goods",
        "quantity": 15,
        "unit_price_usd": 4500.0,
    },
    {
        "id": "cargo-opensky-484161-2",
        "carrier_id": "opensky-484161",
        "commodity": "semiconductors",
        "quantity": 10,
        "unit_price_usd": 9800.0,
    },
    {
        "id": "cargo-opensky-3c6444-1",
        "carrier_id": "opensky-3c6444",
        "commodity": "industrial_chemicals",
        "quantity": 20,
        "unit_price_usd": 750.0,
    },
    {
        "id": "cargo-opensky-3c6444-2",
        "carrier_id": "opensky-3c6444",
        "commodity": "spare_engines",
        "quantity": 1,
        "unit_price_usd": 125000.0,
    },
]

for _item in CARGO:
    _item["value_usd"] = float(_item["quantity"]) * float(_item["unit_price_usd"])

# Spatial scope for the UK airspace closure — live PostGIS entities inside
# this envelope become AFFECTED_BY on every query / sync (not a fixed mock list).
UK_AFFECT_BBOX = format_bbox(UK_AIRSPACE_BBOX)

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


async def _seed_postgres(settings: Settings) -> None:
    """Upsert demo aircraft and cargo into PostGIS live store."""
    logger.info(
        "Upserting %d aircraft + %d cargo into Postgres …",
        len(AIRCRAFT),
        len(CARGO),
    )
    pool = await create_pool(settings)
    now = datetime.now(tz=timezone.utc)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for ac in AIRCRAFT:
                    entity = CanonicalEntity(
                        id=ac["id"],
                        type="moving_entity",
                        timestamp=now,
                        status=ac["status"],
                        geometry={
                            "type": "Point",
                            "coordinates": [ac["lon"], ac["lat"]],
                        },
                        attributes={
                            "call_sign": ac["callsign"],
                            "origin_country": ac["origin"],
                            "route": ac["route"],
                            "revenue_usd": ac["revenue_usd"],
                        },
                    )
                    await _upsert_entity(conn, entity)
                    await _insert_state(conn, entity)
                    logger.info(
                        "  ✓ %s (%s) @ %.2f,%.2f revenue=$%.0f",
                        ac["id"],
                        ac["callsign"],
                        ac["lon"],
                        ac["lat"],
                        ac["revenue_usd"],
                    )

                for item in CARGO:
                    entity = CanonicalEntity(
                        id=item["id"],
                        type="cargo_item",
                        timestamp=now,
                        status="in_transit",
                        geometry=None,
                        attributes={
                            "commodity": item["commodity"],
                            "quantity": item["quantity"],
                            "unit_price_usd": item["unit_price_usd"],
                            "value_usd": item["value_usd"],
                            "carrier_id": item["carrier_id"],
                        },
                    )
                    await _upsert_entity(conn, entity)
                    await _insert_state(conn, entity)
                    logger.info(
                        "  ✓ %s (%s) value=$%.0f on %s",
                        item["id"],
                        item["commodity"],
                        item["value_usd"],
                        item["carrier_id"],
                    )
    finally:
        await pool.close()
    logger.info("Postgres seed complete.")


async def _seed_neo4j(settings: Settings) -> None:
    logger.info("Connecting to Neo4j at %s …", settings.neo4j_uri)
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        async with driver.session(database="neo4j") as session:
            logger.info("Creating %d aircraft Entity nodes …", len(AIRCRAFT))
            for ac in AIRCRAFT:
                await session.run(
                    "MERGE (n:Entity {id: $id}) "
                    "SET n.type = $type, n.callsign = $callsign, "
                    "    n.origin = $origin, n.route = $route, "
                    "    n.revenue_usd = $revenue_usd",
                    id=ac["id"],
                    type="moving_entity",
                    callsign=ac["callsign"],
                    origin=ac["origin"],
                    route=ac["route"],
                    revenue_usd=ac["revenue_usd"],
                )
                logger.info("  ✓ %s (%s) — %s", ac["id"], ac["callsign"], ac["route"])

            logger.info("Creating %d cargo Entity nodes …", len(CARGO))
            for item in CARGO:
                await session.run(
                    "MERGE (n:Entity {id: $id}) "
                    "SET n.type = $type, n.commodity = $commodity, "
                    "    n.quantity = $quantity, "
                    "    n.unit_price_usd = $unit_price_usd, "
                    "    n.value_usd = $value_usd, "
                    "    n.carrier_id = $carrier_id",
                    id=item["id"],
                    type="cargo_item",
                    commodity=item["commodity"],
                    quantity=item["quantity"],
                    unit_price_usd=item["unit_price_usd"],
                    value_usd=item["value_usd"],
                    carrier_id=item["carrier_id"],
                )
                await session.run(
                    "MATCH (carrier:Entity {id: $carrier_id}), "
                    "      (cargo:Entity {id: $cargo_id}) "
                    f"MERGE (carrier)-[:{EDGE_CARRIES}]->(cargo)",
                    carrier_id=item["carrier_id"],
                    cargo_id=item["id"],
                )
                logger.info(
                    "  ✓ %s -[%s]-> %s",
                    item["carrier_id"],
                    EDGE_CARRIES,
                    item["id"],
                )

            logger.info("Wiring %d dependency edges …", len(DEPENDENCIES))
            for from_id, to_id in DEPENDENCIES:
                await session.run(
                    "MATCH (a:Entity {id: $from_id}), (b:Entity {id: $to_id}) "
                    "MERGE (a)-[:DEPENDS_ON]->(b)",
                    from_id=from_id,
                    to_id=to_id,
                )
                logger.info("  ✓ %s → %s", from_id, to_id)

            logger.info("Injecting SimulationEvent: %s …", EVENT_ID)
            await session.run(
                "MERGE (e:SimulationEvent {id: $id}) "
                "SET e.scenario_id = $scenario_id, "
                "    e.description = $description, "
                "    e.affect_bbox = $bbox",
                id=EVENT_ID,
                scenario_id=SCENARIO_ID,
                description=EVENT_DESCRIPTION[:200],
                bbox=UK_AFFECT_BBOX,
            )
            logger.info("  ✓ event affect_bbox=%s", UK_AFFECT_BBOX)
    finally:
        await driver.close()


async def _sync_spatial_overlay(settings: Settings) -> None:
    """Wire AFFECTED_BY from live PostGIS entities inside the UK FIR bbox."""
    from src.graph.spatial_overlay import sync_event_affected_from_bbox

    pool = await create_pool(settings)
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        affected = await sync_event_affected_from_bbox(
            driver,
            pool,
            event_id=EVENT_ID,
            bbox=UK_AFFECT_BBOX,
        )
        logger.info(
            "Spatial overlay synced: %d live entities affected in scenario '%s'.",
            len(affected),
            SCENARIO_ID,
        )
    finally:
        await driver.close()
        await pool.close()


async def main() -> None:
    settings = Settings()
    if not settings.neo4j_password:
        raise SystemExit(
            "NEO4J_PASSWORD is not set. Copy .env.example to .env at the repo root."
        )

    await _seed_postgres(settings)
    await _seed_neo4j(settings)
    await _sync_spatial_overlay(settings)

    logger.info("\nRun this query to test:")
    logger.info('  scenario_id: "%s"', SCENARIO_ID)
    logger.info(
        '  question:    "UK airspace is closed due to a NATS GPS failure. '
        "Which aircraft are affected, what diversions should be issued, "
        'and what is the estimated cost of impact?"'
    )


if __name__ == "__main__":
    asyncio.run(main())
