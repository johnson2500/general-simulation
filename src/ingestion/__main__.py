"""CLI entry point for the ingestion runner.

Run one full ingestion cycle and exit.  Designed for an OpenShift CronJob:

    uv run python -m src.ingestion [--adapter ADAPTER_ID]

Or via the pyproject.toml script:

    uv run ingest-run [--adapter ADAPTER_ID]

Adapters available depend on ``ENABLED_DOMAINS`` (see Settings).

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
from src.ingestion.registry import get_adapter_class, list_adapter_ids
from src.ingestion.runner import run_ingestion

logger = logging.getLogger(__name__)


def _parse_args(settings: Settings) -> argparse.Namespace:
    available = list_adapter_ids(settings)
    if not available:
        raise SystemExit(
            "No adapters available. Set ENABLED_DOMAINS to a known domain "
            "(e.g. aviation, shipping)."
        )

    default_adapter = (
        "opensky_flights"
        if "opensky_flights" in available
        else available[0]
    )

    parser = argparse.ArgumentParser(
        description="Run one ingestion cycle and upsert results into the live store."
    )
    parser.add_argument(
        "--adapter",
        default=default_adapter,
        choices=available,
        help=f"Adapter to run (default: {default_adapter}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args()


async def _main(adapter_id: str, settings: Settings) -> int:
    pool = await create_pool(settings)
    neo4j_driver = create_neo4j_driver(settings)
    try:
        adapter_cls = get_adapter_class(adapter_id, settings)
        adapter = adapter_cls()
        count = await run_ingestion(adapter, pool, neo4j_driver=neo4j_driver)
        print(f"Ingestion complete: adapter={adapter_id} entities={count}")
        return 0
    finally:
        await pool.close()
        await neo4j_driver.close()


def main() -> None:
    settings = Settings()
    args = _parse_args(settings)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s — %(message)s",
    )
    sys.exit(asyncio.run(_main(args.adapter, settings)))


if __name__ == "__main__":
    main()
