from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.core.config import Settings

router = APIRouter()


def get_settings() -> Settings:
    return Settings()


async def _check_db(dsn: str) -> bool:
    """Attempt a single round-trip to Postgres; return True on success."""
    try:
        conn = await asyncpg.connect(dsn, timeout=3)
        await conn.execute("SELECT 1")
        await conn.close()
        return True
    except Exception:
        return False


@router.get("/livez")
async def livez() -> dict[str, str]:
    """Process liveness — does not depend on database connectivity."""
    return {"status": "ok"}


@router.get("/health", response_model=None)
async def health(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any] | JSONResponse:
    """Readiness probe.

    Returns HTTP 503 until the lifespan pool is available and Postgres
    accepts a connection, so kube readiness does not route traffic to a
    half-started API.
    """
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        return JSONResponse(
            status_code=503,
            content={"status": "starting", "db": "pool_unavailable"},
        )

    db_ok = await _check_db(settings.postgres_dsn)
    body = {
        "status": "ok" if db_ok else "degraded",
        "db": "reachable" if db_ok else "unreachable",
    }
    if not db_ok:
        return JSONResponse(status_code=503, content=body)
    return body
