"""Tests for domain catalog / ENABLED_DOMAINS registry."""
from __future__ import annotations

import pytest

from src.core.config import Settings
from src.ingestion.registry import (
    DOMAIN_CATALOG,
    get_adapter_registry,
    list_adapter_ids,
    list_enabled_domain_ids,
    resolve_solver,
)
from src.solver.stub import StubSolver


def test_catalog_has_aviation_and_shipping():
    assert "aviation" in DOMAIN_CATALOG
    assert "shipping" in DOMAIN_CATALOG
    assert "earthquakes" not in DOMAIN_CATALOG
    assert "opensky_flights" in DOMAIN_CATALOG["aviation"].adapters
    assert "shipping_demo" in DOMAIN_CATALOG["shipping"].adapters


def test_default_enabled_domains_is_aviation_and_shipping():
    settings = Settings(enabled_domains="aviation,shipping")
    assert list_enabled_domain_ids(settings) == ["aviation", "shipping"]
    assert set(list_adapter_ids(settings)) == {"opensky_flights", "shipping_demo"}


def test_enabled_domains_loads_both_adapters():
    settings = Settings(enabled_domains="aviation,shipping")
    assert set(list_adapter_ids(settings)) == {
        "opensky_flights",
        "shipping_demo",
    }
    registry = get_adapter_registry(settings)
    assert registry["opensky_flights"].adapter_id == "opensky_flights"
    assert registry["shipping_demo"].adapter_id == "shipping_demo"


def test_unknown_domain_raises():
    settings = Settings(enabled_domains="aviation,not_a_domain")
    with pytest.raises(ValueError, match="Unknown domain"):
        list_enabled_domain_ids(settings)


def test_parsed_enabled_domains_normalizes():
    settings = Settings(enabled_domains=" Aviation , Shipping ")
    assert settings.parsed_enabled_domains == ["aviation", "shipping"]


def test_resolve_solver_defaults_to_stub():
    settings = Settings(enabled_domains="aviation")
    solver = resolve_solver(settings)
    assert isinstance(solver, StubSolver)
