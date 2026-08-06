"""Unit tests for startup wait helpers in ``src.core.db``."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.config import Settings


@pytest.mark.asyncio
async def test_wait_for_pool_retries_then_succeeds():
    from src.core.db import wait_for_pool

    settings = Settings(
        postgres_dsn="postgresql://mock:mock@localhost/mock",
        startup_db_wait_seconds=5,
        startup_db_wait_interval_seconds=0.01,
    )
    pool = object()
    create = AsyncMock(side_effect=[ConnectionRefusedError("down"), pool])

    with patch("src.core.db.create_pool", create):
        result = await wait_for_pool(settings)

    assert result is pool
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_wait_for_pool_raises_after_timeout():
    from src.core.db import wait_for_pool

    settings = Settings(
        postgres_dsn="postgresql://mock:mock@localhost/mock",
        startup_db_wait_seconds=0.05,
        startup_db_wait_interval_seconds=0.01,
    )
    create = AsyncMock(side_effect=ConnectionRefusedError("down"))

    with patch("src.core.db.create_pool", create):
        with pytest.raises(RuntimeError, match="Postgres unavailable"):
            await wait_for_pool(settings)


@pytest.mark.asyncio
async def test_wait_for_neo4j_retries_then_succeeds():
    from src.core.db import wait_for_neo4j

    settings = Settings(
        neo4j_uri="bolt://localhost:7687",
        neo4j_password="pw",
        startup_db_wait_seconds=5,
        startup_db_wait_interval_seconds=0.01,
    )
    driver = MagicMock()
    driver.verify_connectivity = AsyncMock(
        side_effect=[ConnectionRefusedError("down"), None]
    )
    driver.close = AsyncMock()

    with patch("src.core.db.create_neo4j_driver", return_value=driver):
        result = await wait_for_neo4j(settings)

    assert result is driver
    assert driver.verify_connectivity.await_count == 2
