"""CLI entry point for the ingestion runner.

Run one full ingestion cycle and exit.  Designed for an OpenShift CronJob:

    uv run python -m src.ingestion [--adapter ADAPTER_ID]

Or via the pyproject.toml script:

    uv run ingest-run [--adapter ADAPTER_ID]

Exit codes:
    0  — success (zero or more entities upserted)
    1  — unhandled error
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.core.config import Settings
from src.core.db import create_neo4j_driver, create_pool
from src.ingestion.adapters.opensky_flights import OpenSkyFlightsAdapter
from src.ingestion.adapters.usgs_earthquakes import USGSEarthquakeAdapter
from src.ingestion.runner import run_ingestion

logger = logging.getLogger(__name__)

_ADAPTERS = {
    "usgs_earthquakes": USGSEarthquakeAdapter,
    "opensky_flights": OpenSkyFlightsAdapter,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one ingestion cycle and upsert results into the live store."
    )
    parser.add_argument(
        "--adapter",
        default="opensky_flights",
        choices=list(_ADAPTERS),
        help="Adapter to run (default: opensky_flights).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args()


async def _main(adapter_id: str) -> int:
    settings = Settings()
    pool = await create_pool(settings)
    neo4j_driver = create_neo4j_driver(settings)
    try:
        adapter_cls = _ADAPTERS[adapter_id]
        adapter = adapter_cls()
        count = await run_ingestion(adapter, pool, neo4j_driver=neo4j_driver)
        print(f"Ingestion complete: adapter={adapter_id} entities={count}")
        return 0
    finally:
        await pool.close()
        await neo4j_driver.close()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s — %(message)s",
    )
    sys.exit(asyncio.run(_main(args.adapter)))


if __name__ == "__main__":
    main()
