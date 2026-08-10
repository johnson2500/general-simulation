"""Seed Postgres + Neo4j with the synthetic shipping domain demo.

Runs the ``shipping_demo`` adapter (fixture-backed), wires dependency edges,
injects the LA port-closure simulation event, and syncs spatial AFFECTED_BY
overlays from the live store.

Run from the repo root:
    uv run python scripts/seed_shipping.py

Requires ENABLED_DOMAINS to include ``shipping`` (default after this branch).
"""
from __future__ import annotations

import asyncio
import logging

from neo4j import AsyncGraphDatabase

from domain.shipping.adapters.shipping_demo import ShippingDemoAdapter
from domain.shipping.bootstrap_graph import (
    EVENT_ID,
    LA_PORT_BBOX,
    SCENARIO_ID,
    bootstrap_shipping_graph,
)
from src.core.config import Settings
from src.core.db import create_pool
from src.graph.spatial_overlay import format_bbox, sync_event_affected_from_bbox
from src.ingestion.runner import run_ingestion

logging.basicConfig(level=logging.INFO, format="%(levelname)s — %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings()
    if not settings.neo4j_password:
        raise SystemExit(
            "NEO4J_PASSWORD is not set. Copy .env.example to .env at the repo root."
        )

    if "shipping" not in settings.parsed_enabled_domains:
        raise SystemExit(
            "ENABLED_DOMAINS must include 'shipping'. "
            f"Current: {settings.enabled_domains!r}"
        )

    pool = await create_pool(settings)
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )
    try:
        adapter = ShippingDemoAdapter()
        count = await run_ingestion(adapter, pool, neo4j_driver=driver)
        logger.info("Ingested %d shipping entities.", count)

        summary = await bootstrap_shipping_graph(driver)
        logger.info("Graph bootstrap: %s", summary)

        bbox = format_bbox(LA_PORT_BBOX)
        affected = await sync_event_affected_from_bbox(
            driver,
            pool,
            event_id=EVENT_ID,
            bbox=bbox,
        )
        logger.info(
            "Spatial overlay synced: %d live entities affected in scenario '%s'.",
            len(affected),
            SCENARIO_ID,
        )
    finally:
        await driver.close()
        await pool.close()

    logger.info("\nRun this query to test:")
    logger.info('  scenario_id: "%s"', SCENARIO_ID)
    logger.info(
        '  question:    "Port of Los Angeles is closed due to a strike. '
        "Which vessels and shipments are affected, what diversions should "
        'we issue, and what is the estimated cost of impact?"'
    )


if __name__ == "__main__":
    asyncio.run(main())
