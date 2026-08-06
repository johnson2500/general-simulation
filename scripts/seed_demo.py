"""Seed Neo4j + Postgres with demo aircraft, maritime assets, and scenarios.

Simulates OpenSky aircraft in European airspace plus Port of LA / Suez maritime
entities, wires dependency and cargo edges, injects simulation event overlays,
and upserts matching PostGIS points for the Supply Chain Map.

Scenarios match ai-supply-chain-agent frontend presets:
  - opensky-uk-closure-001          (Trigger World Event / UK airspace)
  - supply-chain-port-strike-la     (Port Strike LA)
  - supply-chain-suez-blockage      (Suez Blockage)

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

# ---------------------------------------------------------------------------
# Maritime demo — Port Strike LA + Suez Blockage (matches supply-chain UI presets)
# ---------------------------------------------------------------------------

FACILITIES = [
    {
        "id": "port-los-angeles",
        "name": "Port of Los Angeles",
        "region": "west_coast",
        "lon": -118.27,
        "lat": 33.74,
        "value_usd": 4_200_000.0,
    },
    {
        "id": "port-long-beach",
        "name": "Port of Long Beach",
        "region": "west_coast",
        "lon": -118.20,
        "lat": 33.75,
        "value_usd": 3_100_000.0,
    },
    {
        "id": "warehouse-inland-empire",
        "name": "Inland Empire DC",
        "region": "west_coast",
        "lon": -117.43,
        "lat": 34.05,
        "value_usd": 1_200_000.0,
    },
    {
        "id": "port-suez",
        "name": "Suez Canal Authority Hub",
        "region": "suez",
        "lon": 32.35,
        "lat": 30.45,
        "value_usd": 8_500_000.0,
    },
    {
        "id": "port-rotterdam",
        "name": "Port of Rotterdam",
        "region": "europe",
        "lon": 4.48,
        "lat": 51.95,
        "value_usd": 5_000_000.0,
    },
]

VESSELS = [
    {
        "id": "vessel-pacific-star",
        "name": "Pacific Star",
        "route": "SHA-LAX",
        "status": "in_transit",
        "lon": -125.5,
        "lat": 32.8,
        "revenue_usd": 890_000.0,
        "depends_on_port": "port-los-angeles",
    },
    {
        "id": "vessel-westbound-express",
        "name": "Westbound Express",
        "route": "YOK-LGB",
        "status": "in_transit",
        "lon": -122.8,
        "lat": 31.5,
        "revenue_usd": 720_000.0,
        "depends_on_port": "port-long-beach",
    },
    {
        "id": "vessel-red-sea-carrier",
        "name": "Red Sea Carrier",
        "route": "SHA-ROT via Suez",
        "status": "in_transit",
        "lon": 33.2,
        "lat": 28.8,
        "revenue_usd": 1_450_000.0,
        "depends_on_port": "port-suez",
    },
    {
        "id": "vessel-med-link",
        "name": "Med Link",
        "route": "SIN-ROT via Suez",
        "status": "in_transit",
        "lon": 32.8,
        "lat": 30.1,
        "revenue_usd": 980_000.0,
        "depends_on_port": "port-suez",
    },
]

# Air freight tied into Port Strike LA / Suez scenarios (pulled via DEPENDS_ON).
CORRIDOR_AIRCRAFT = [
    {
        "id": "opensky-a4e301",
        "callsign": "FDX182",
        "origin": "United States",
        "route": "ANC-LAX",
        "status": "airborne",
        "lon": -118.45,
        "lat": 33.95,
        "revenue_usd": 410_000.0,
        "depends_on_port": "port-los-angeles",
    },
    {
        "id": "opensky-a19ce0",
        "callsign": "UPS905",
        "origin": "United States",
        "route": "HNL-LGB",
        "status": "airborne",
        "lon": -118.10,
        "lat": 33.82,
        "revenue_usd": 365_000.0,
        "depends_on_port": "port-long-beach",
    },
    {
        "id": "opensky-8961e2",
        "callsign": "UAE817",
        "origin": "United Arab Emirates",
        "route": "DXB-FRA",
        "status": "airborne",
        "lon": 33.5,
        "lat": 29.2,
        "revenue_usd": 780_000.0,
        "depends_on_port": "port-suez",
    },
    {
        "id": "opensky-75804b",
        "callsign": "CPA328",
        "origin": "Hong Kong",
        "route": "HKG-AMS via Suez corridor",
        "status": "airborne",
        "lon": 32.6,
        "lat": 30.8,
        "revenue_usd": 695_000.0,
        "depends_on_port": "port-suez",
    },
]

MARITIME_CARGO: list[dict] = [
    {
        "id": "cargo-pacific-star-electronics",
        "carrier_id": "vessel-pacific-star",
        "commodity": "electronics",
        "quantity": 200,
        "unit_price_usd": 1500.0,
    },
    {
        "id": "cargo-pacific-star-auto",
        "carrier_id": "vessel-pacific-star",
        "commodity": "automotive_parts",
        "quantity": 90,
        "unit_price_usd": 2200.0,
    },
    {
        "id": "cargo-westbound-apparel",
        "carrier_id": "vessel-westbound-express",
        "commodity": "apparel",
        "quantity": 500,
        "unit_price_usd": 90.0,
    },
    {
        "id": "cargo-westbound-furniture",
        "carrier_id": "vessel-westbound-express",
        "commodity": "furniture",
        "quantity": 120,
        "unit_price_usd": 450.0,
    },
    {
        "id": "cargo-red-sea-auto",
        "carrier_id": "vessel-red-sea-carrier",
        "commodity": "automotive_parts",
        "quantity": 80,
        "unit_price_usd": 2200.0,
    },
    {
        "id": "cargo-red-sea-machinery",
        "carrier_id": "vessel-red-sea-carrier",
        "commodity": "machinery",
        "quantity": 12,
        "unit_price_usd": 18500.0,
    },
    {
        "id": "cargo-med-pharma",
        "carrier_id": "vessel-med-link",
        "commodity": "pharmaceuticals",
        "quantity": 40,
        "unit_price_usd": 8500.0,
    },
    {
        "id": "cargo-med-electronics",
        "carrier_id": "vessel-med-link",
        "commodity": "electronics",
        "quantity": 150,
        "unit_price_usd": 1600.0,
    },
]

CORRIDOR_CARGO: list[dict] = [
    {
        "id": "cargo-opensky-a4e301-1",
        "carrier_id": "opensky-a4e301",
        "commodity": "express_parcels",
        "quantity": 80,
        "unit_price_usd": 420.0,
    },
    {
        "id": "cargo-opensky-a4e301-2",
        "carrier_id": "opensky-a4e301",
        "commodity": "medical_devices",
        "quantity": 15,
        "unit_price_usd": 9800.0,
    },
    {
        "id": "cargo-opensky-a19ce0-1",
        "carrier_id": "opensky-a19ce0",
        "commodity": "perishables",
        "quantity": 60,
        "unit_price_usd": 310.0,
    },
    {
        "id": "cargo-opensky-a19ce0-2",
        "carrier_id": "opensky-a19ce0",
        "commodity": "semiconductors",
        "quantity": 25,
        "unit_price_usd": 7500.0,
    },
    {
        "id": "cargo-opensky-8961e2-1",
        "carrier_id": "opensky-8961e2",
        "commodity": "pharmaceuticals",
        "quantity": 22,
        "unit_price_usd": 9200.0,
    },
    {
        "id": "cargo-opensky-8961e2-2",
        "carrier_id": "opensky-8961e2",
        "commodity": "luxury_goods",
        "quantity": 18,
        "unit_price_usd": 5600.0,
    },
    {
        "id": "cargo-opensky-75804b-1",
        "carrier_id": "opensky-75804b",
        "commodity": "electronics",
        "quantity": 110,
        "unit_price_usd": 1400.0,
    },
    {
        "id": "cargo-opensky-75804b-2",
        "carrier_id": "opensky-75804b",
        "commodity": "aerospace_parts",
        "quantity": 8,
        "unit_price_usd": 24000.0,
    },
]

for _item in MARITIME_CARGO + CORRIDOR_CARGO:
    _item["value_usd"] = float(_item["quantity"]) * float(_item["unit_price_usd"])

# Spatial scope for the UK airspace closure — live PostGIS entities inside
# this envelope become AFFECTED_BY on every query / sync (not a fixed mock list).
UK_AFFECT_BBOX = format_bbox(UK_AIRSPACE_BBOX)
LA_PORTS_BBOX = format_bbox((-118.6, 33.6, -117.3, 34.2))
SUEZ_CORRIDOR_BBOX = format_bbox((32.0, 27.5, 34.5, 31.5))

DEPENDENCIES = [
    ("opensky-407290", "opensky-471f52"),  # both on LHR North Atlantic slots
    ("opensky-4ca87e", "opensky-407290"),  # DUB-BOS feeds same NATS track
    ("opensky-3c4b58", "opensky-3c6444"),  # MUC-LHR feeds FRA-ORD connection
    ("opensky-40617d", "opensky-484161"),  # LGW-FCO shares Med corridor with CDG-LAX
    ("vessel-pacific-star", "port-los-angeles"),
    ("vessel-westbound-express", "port-long-beach"),
    ("warehouse-inland-empire", "port-los-angeles"),
    ("warehouse-inland-empire", "port-long-beach"),
    ("vessel-red-sea-carrier", "port-suez"),
    ("vessel-med-link", "port-suez"),
    ("port-rotterdam", "port-suez"),  # Europe inbound depends on Suez throughput
    ("opensky-a4e301", "port-los-angeles"),
    ("opensky-a19ce0", "port-long-beach"),
    ("opensky-8961e2", "port-suez"),
    ("opensky-75804b", "port-suez"),
    ("opensky-a4e301", "warehouse-inland-empire"),
    ("opensky-a19ce0", "warehouse-inland-empire"),
]

# Scenario IDs match ai-supply-chain-agent frontend presets
# (Port Strike LA, Suez Blockage, Trigger World Event / UK closure).
SCENARIOS = [
    {
        "scenario_id": "opensky-uk-closure-001",
        "event_id": "evt-uk-airspace-closure-20260630",
        "bbox": UK_AFFECT_BBOX,
        "description": (
            "UK airspace has been closed to all civilian traffic effective 13:00 UTC "
            "on 30 June 2026 due to a critical GPS/navigation system failure affecting "
            "NATS (National Air Traffic Services). All aircraft currently airborne in "
            "UK airspace (London FIR and Scottish FIR) must divert immediately. "
            "Inbound flights to LHR, LGW, MAN, and EDI are suspended. "
            "Transatlantic traffic on NATS tracks is rerouted via oceanic contingency "
            "tracks further north or through Shanwick/Gander delegation."
        ),
    },
    {
        "scenario_id": "supply-chain-port-strike-la",
        "event_id": "evt-port-strike-la-2026",
        "bbox": LA_PORTS_BBOX,
        "description": (
            "Port strike at Los Angeles and Long Beach. Labor action has halted "
            "container operations at both West Coast hubs. Inbound Asia–US vessels "
            "are delayed; inland distribution centers depending on LA/LGB faces "
            "inventory shortfalls within 72 hours. Reroute options include Oakland "
            "and Prince Rupert with multi-day rail delays."
        ),
    },
    {
        "scenario_id": "supply-chain-suez-blockage",
        "event_id": "evt-suez-blockage-2026",
        "bbox": SUEZ_CORRIDOR_BBOX,
        "description": (
            "Suez Canal blockage. A grounded vessel has closed the canal corridor. "
            "Asia–Europe sea freight is delayed approximately 14 days if diverted "
            "around the Cape of Good Hope. Value at risk includes high-value "
            "automotive and pharmaceutical cargoes plus downstream European port "
            "throughput (Rotterdam)."
        ),
    },
]

# Back-compat aliases used by logging / older docs.
SCENARIO_ID = SCENARIOS[0]["scenario_id"]
EVENT_ID = SCENARIOS[0]["event_id"]
EVENT_DESCRIPTION = SCENARIOS[0]["description"]


async def _seed_postgres(settings: Settings) -> None:
    """Upsert demo aircraft, facilities, vessels, and cargo into PostGIS live store."""
    all_aircraft = AIRCRAFT + CORRIDOR_AIRCRAFT
    all_cargo = CARGO + MARITIME_CARGO + CORRIDOR_CARGO
    logger.info(
        "Upserting %d aircraft + %d facilities + %d vessels + %d cargo into Postgres …",
        len(all_aircraft),
        len(FACILITIES),
        len(VESSELS),
        len(all_cargo),
    )
    pool = await create_pool(settings)
    now = datetime.now(tz=timezone.utc)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                for ac in all_aircraft:
                    attrs = {
                        "call_sign": ac["callsign"],
                        "origin_country": ac["origin"],
                        "route": ac["route"],
                        "revenue_usd": ac["revenue_usd"],
                    }
                    if ac.get("depends_on_port"):
                        attrs["depends_on_port"] = ac["depends_on_port"]
                    entity = CanonicalEntity(
                        id=ac["id"],
                        type="moving_entity",
                        timestamp=now,
                        status=ac["status"],
                        geometry={
                            "type": "Point",
                            "coordinates": [ac["lon"], ac["lat"]],
                        },
                        attributes=attrs,
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

                for facility in FACILITIES:
                    entity = CanonicalEntity(
                        id=facility["id"],
                        type="facility",
                        timestamp=now,
                        status="operational",
                        geometry={
                            "type": "Point",
                            "coordinates": [facility["lon"], facility["lat"]],
                        },
                        attributes={
                            "name": facility["name"],
                            "region": facility["region"],
                            "value_usd": facility["value_usd"],
                        },
                    )
                    await _upsert_entity(conn, entity)
                    await _insert_state(conn, entity)
                    logger.info(
                        "  ✓ %s (%s) value=$%.0f",
                        facility["id"],
                        facility["name"],
                        facility["value_usd"],
                    )

                for vessel in VESSELS:
                    entity = CanonicalEntity(
                        id=vessel["id"],
                        type="moving_entity",
                        timestamp=now,
                        status=vessel["status"],
                        geometry={
                            "type": "Point",
                            "coordinates": [vessel["lon"], vessel["lat"]],
                        },
                        attributes={
                            "name": vessel["name"],
                            "route": vessel["route"],
                            "revenue_usd": vessel["revenue_usd"],
                            "depends_on_port": vessel["depends_on_port"],
                        },
                    )
                    await _upsert_entity(conn, entity)
                    await _insert_state(conn, entity)
                    logger.info(
                        "  ✓ %s (%s) @ %.2f,%.2f revenue=$%.0f",
                        vessel["id"],
                        vessel["name"],
                        vessel["lon"],
                        vessel["lat"],
                        vessel["revenue_usd"],
                    )

                for item in all_cargo:
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
    all_aircraft = AIRCRAFT + CORRIDOR_AIRCRAFT
    all_cargo = CARGO + MARITIME_CARGO + CORRIDOR_CARGO
    try:
        async with driver.session(database="neo4j") as session:
            logger.info("Creating %d aircraft Entity nodes …", len(all_aircraft))
            for ac in all_aircraft:
                await session.run(
                    "MERGE (n:Entity {id: $id}) "
                    "SET n.type = $type, n.callsign = $callsign, "
                    "    n.origin = $origin, n.route = $route, "
                    "    n.revenue_usd = $revenue_usd, "
                    "    n.depends_on_port = $depends_on_port",
                    id=ac["id"],
                    type="moving_entity",
                    callsign=ac["callsign"],
                    origin=ac["origin"],
                    route=ac["route"],
                    revenue_usd=ac["revenue_usd"],
                    depends_on_port=ac.get("depends_on_port"),
                )
                logger.info("  ✓ %s (%s) — %s", ac["id"], ac["callsign"], ac["route"])

            logger.info("Creating %d facility Entity nodes …", len(FACILITIES))
            for facility in FACILITIES:
                await session.run(
                    "MERGE (n:Entity {id: $id}) "
                    "SET n.type = $type, n.name = $name, "
                    "    n.region = $region, n.value_usd = $value_usd",
                    id=facility["id"],
                    type="facility",
                    name=facility["name"],
                    region=facility["region"],
                    value_usd=facility["value_usd"],
                )
                logger.info("  ✓ %s (%s)", facility["id"], facility["name"])

            logger.info("Creating %d vessel Entity nodes …", len(VESSELS))
            for vessel in VESSELS:
                await session.run(
                    "MERGE (n:Entity {id: $id}) "
                    "SET n.type = $type, n.name = $name, "
                    "    n.route = $route, n.revenue_usd = $revenue_usd, "
                    "    n.depends_on_port = $depends_on_port",
                    id=vessel["id"],
                    type="moving_entity",
                    name=vessel["name"],
                    route=vessel["route"],
                    revenue_usd=vessel["revenue_usd"],
                    depends_on_port=vessel["depends_on_port"],
                )
                logger.info("  ✓ %s (%s) — %s", vessel["id"], vessel["name"], vessel["route"])

            logger.info("Creating %d cargo Entity nodes …", len(all_cargo))
            for item in all_cargo:
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

            logger.info("Injecting %d SimulationEvent overlays …", len(SCENARIOS))
            for scenario in SCENARIOS:
                await session.run(
                    "MERGE (e:SimulationEvent {id: $id}) "
                    "SET e.scenario_id = $scenario_id, "
                    "    e.description = $description, "
                    "    e.affect_bbox = $bbox",
                    id=scenario["event_id"],
                    scenario_id=scenario["scenario_id"],
                    description=scenario["description"][:200],
                    bbox=scenario["bbox"],
                )
                logger.info(
                    "  ✓ %s (%s) affect_bbox=%s",
                    scenario["event_id"],
                    scenario["scenario_id"],
                    scenario["bbox"],
                )
    finally:
        await driver.close()


async def _sync_spatial_overlay(settings: Settings) -> None:
    """Wire AFFECTED_BY from live PostGIS entities inside each scenario bbox."""
    from src.graph.spatial_overlay import sync_event_affected_from_bbox

    pool = await create_pool(settings)
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        for scenario in SCENARIOS:
            affected = await sync_event_affected_from_bbox(
                driver,
                pool,
                event_id=scenario["event_id"],
                bbox=scenario["bbox"],
            )
            logger.info(
                "Spatial overlay synced: %d live entities affected in scenario '%s'.",
                len(affected),
                scenario["scenario_id"],
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

    logger.info("\nSeeded scenarios (match supply-chain frontend presets):")
    for scenario in SCENARIOS:
        logger.info('  - %s', scenario["scenario_id"])
    logger.info("\nExample query:")
    logger.info('  scenario_id: "%s"', SCENARIO_ID)
    logger.info(
        '  question:    "UK airspace is closed due to a NATS GPS failure. '
        "Which aircraft are affected, what diversions should be issued, "
        'and what is the estimated cost of impact?"'
    )


if __name__ == "__main__":
    asyncio.run(main())
