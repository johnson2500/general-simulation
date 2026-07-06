"""FastAPI dependency-injection helpers.

Routes declare these as ``Depends(…)`` parameters.  Tests override them via
``app.dependency_overrides[get_pool] = lambda: mock_pool``.
"""
from __future__ import annotations

import asyncpg
from fastapi import Request
from neo4j import AsyncDriver

from src.core.solver import Solver
from src.llm.base import LLMClientBase


def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool  # type: ignore[no-any-return]


def get_neo4j_driver(request: Request) -> AsyncDriver:
    return request.app.state.neo4j_driver  # type: ignore[no-any-return]


def get_llm_client(request: Request) -> LLMClientBase:
    return request.app.state.llm_client  # type: ignore[no-any-return]


def get_solver(request: Request) -> Solver:
    return request.app.state.solver  # type: ignore[no-any-return]
