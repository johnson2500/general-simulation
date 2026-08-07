#!/usr/bin/env python3
"""Pull live OpenSky aircraft from your laptop and upsert into Postgres + Neo4j.

Why local: OpenSky blocks many AWS/hyperscaler IPs, so the in-cluster CronJob
cannot fetch. Your workstation can reach the API; port-forward writes into the
OpenShift gen-sim databases the dashboard already uses.

Each aircraft gets synthetic economics similar to ``seed_demo.py``:
  - ``revenue_usd`` / ``operating_cost_usd`` on the flight
  - 1–2 ``cargo_item`` entities with ``unit_price_usd``, ``quantity``, ``value_usd``
  - Neo4j ``CARRIES`` edges from flight → cargo

Economics are deterministic per entity id (stable across re-runs).

Does not create scenario overlays or maritime assets — run seed_demo.py
(or ``make seed-gen-sim``) first for those.

Usage:

    make seed-opensky-live
    make seed-opensky-live OPENSKY_MAX=500
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import random
from dataclasses import replace
from datetime import datetime, timezone

from domain.aviation.adapters.opensky_flights import OpenSkyFlightsAdapter
from src.core.config import Settings
from src.core.db import create_neo4j_driver, create_pool
from src.core.ingestion import CanonicalEntity
from src.graph.nodes import EDGE_CARRIES
from src.ingestion.runner import _insert_state, _upsert_entity

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)

_COMMODITIES = (
    "electronics",
    "pharma",
    "automotive_parts",
    "aerospace_parts",
    "perishables",
    "semiconductors",
    "medical_devices",
    "luxury_goods",
)


def _rng_for(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _enrich_aircraft(entity: CanonicalEntity) -> CanonicalEntity:
    """Attach seed_demo-style revenue/cost attributes (deterministic per id)."""
    rng = _rng_for(entity.id, "econ")
    revenue = round(rng.uniform(45_000.0, 850_000.0), 2)
    operating_cost = round(revenue * rng.uniform(0.35, 0.75), 2)
    attrs = dict(entity.attributes or {})
    attrs["revenue_usd"] = revenue
    attrs["operating_cost_usd"] = operating_cost
    return replace(entity, attributes=attrs)


def _synthetic_cargo_for(aircraft: CanonicalEntity) -> list[CanonicalEntity]:
    """Create 1–2 cargo_item entities carried by *aircraft* (no geometry)."""
    rng = _rng_for(aircraft.id, "cargo")
    count = rng.randint(1, 2)
    now = aircraft.timestamp if aircraft.timestamp.tzinfo else datetime.now(tz=timezone.utc)
    cargo: list[CanonicalEntity] = []
    for i in range(1, count + 1):
        unit_price = round(rng.uniform(80.0, 125_000.0), 2)
        quantity = rng.randint(1, 40)
        value = round(unit_price * quantity, 2)
        commodity = rng.choice(_COMMODITIES)
        cargo.append(
            CanonicalEntity(
                id=f"cargo-{aircraft.id}-{i}",
                type="cargo_item",
                timestamp=now,
                status="in_transit",
                geometry=None,
                attributes={
                    "carrier_id": aircraft.id,
                    "commodity": commodity,
                    "quantity": quantity,
                    "unit_price_usd": unit_price,
                    "value_usd": value,
                },
            )
        )
    return cargo


async def _upsert_postgres(pool, aircraft: list[CanonicalEntity], cargo: list[CanonicalEntity]) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            for entity in aircraft + cargo:
                await _upsert_entity(conn, entity)
                await _insert_state(conn, entity)


async def _upsert_neo4j(driver, aircraft: list[CanonicalEntity], cargo: list[CanonicalEntity]) -> None:
    """Merge flights + cargo with economics; wire CARRIES edges (seed_demo parity)."""
    async with driver.session(database="neo4j") as session:
        for ac in aircraft:
            attrs = ac.attributes or {}
            await session.run(
                "MERGE (n:Entity {id: $id}) "
                "SET n.type = $type, "
                "    n.callsign = $callsign, "
                "    n.origin = $origin, "
                "    n.revenue_usd = $revenue_usd, "
                "    n.operating_cost_usd = $operating_cost_usd",
                id=ac.id,
                type=ac.type,
                callsign=attrs.get("call_sign"),
                origin=attrs.get("origin_country"),
                revenue_usd=attrs.get("revenue_usd"),
                operating_cost_usd=attrs.get("operating_cost_usd"),
            )

        for item in cargo:
            attrs = item.attributes or {}
            await session.run(
                "MERGE (n:Entity {id: $id}) "
                "SET n.type = $type, "
                "    n.commodity = $commodity, "
                "    n.quantity = $quantity, "
                "    n.unit_price_usd = $unit_price_usd, "
                "    n.value_usd = $value_usd, "
                "    n.carrier_id = $carrier_id",
                id=item.id,
                type=item.type,
                commodity=attrs.get("commodity"),
                quantity=attrs.get("quantity"),
                unit_price_usd=attrs.get("unit_price_usd"),
                value_usd=attrs.get("value_usd"),
                carrier_id=attrs.get("carrier_id"),
            )
            await session.run(
                "MATCH (carrier:Entity {id: $carrier_id}), "
                "      (cargo:Entity {id: $cargo_id}) "
                f"MERGE (carrier)-[:{EDGE_CARRIES}]->(cargo)",
                carrier_id=attrs.get("carrier_id"),
                cargo_id=item.id,
            )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch live OpenSky states locally and upsert into Postgres + Neo4j.",
    )
    parser.add_argument(
        "--max",
        type=int,
        default=2000,
        help="Max aircraft to upsert after normalize (default: 2000; 0 = unlimited).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="OpenSky HTTP timeout seconds (default: 60).",
    )
    parser.add_argument(
        "--no-cargo",
        action="store_true",
        help="Skip synthetic cargo_item entities and CARRIES edges.",
    )
    return parser.parse_args()


async def _main(max_entities: int, timeout: float, with_cargo: bool) -> int:
    settings = Settings()
    if not settings.neo4j_password:
        raise SystemExit(
            "NEO4J_PASSWORD is not set. Use make seed-opensky-live or export cluster secrets."
        )
    if not settings.postgres_dsn:
        raise SystemExit("POSTGRES_DSN is not set.")

    logger.info("Fetching OpenSky /api/states/all from this machine…")
    adapter = OpenSkyFlightsAdapter(timeout_seconds=timeout)
    raw = await adapter.fetch()
    aircraft = adapter.normalize(raw)
    if max_entities > 0 and len(aircraft) > max_entities:
        logger.info(
            "Capping aircraft: fetched=%d max=%d (pass --max 0 for unlimited)",
            len(aircraft),
            max_entities,
        )
        aircraft = aircraft[:max_entities]

    aircraft = [_enrich_aircraft(ac) for ac in aircraft]
    cargo: list[CanonicalEntity] = []
    if with_cargo:
        for ac in aircraft:
            cargo.extend(_synthetic_cargo_for(ac))

    logger.info(
        "Upserting %d aircraft + %d cargo (with revenue/cost) into Postgres + Neo4j…",
        len(aircraft),
        len(cargo),
    )

    pool = await create_pool(settings)
    neo4j_driver = create_neo4j_driver(settings)
    try:
        await _upsert_postgres(pool, aircraft, cargo)
        await _upsert_neo4j(neo4j_driver, aircraft, cargo)
    finally:
        await pool.close()
        await neo4j_driver.close()

    sample = aircraft[0].attributes if aircraft else {}
    logger.info(
        "Done. sample revenue_usd=%s operating_cost_usd=%s cargo=%d",
        sample.get("revenue_usd"),
        sample.get("operating_cost_usd"),
        len(cargo),
    )
    return 0


def main() -> None:
    args = _parse_args()
    try:
        code = asyncio.run(_main(args.max, args.timeout, with_cargo=not args.no_cargo))
    except Exception:
        logger.exception("OpenSky live seed failed")
        raise SystemExit(1) from None
    raise SystemExit(code)


if __name__ == "__main__":
    main()
