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


def test_catalog_has_aviation_and_earthquakes():
    assert "aviation" in DOMAIN_CATALOG
    assert "earthquakes" in DOMAIN_CATALOG
    assert "opensky_flights" in DOMAIN_CATALOG["aviation"].adapters
    assert "usgs_earthquakes" in DOMAIN_CATALOG["earthquakes"].adapters


def test_default_enabled_domains_is_aviation():
    settings = Settings(enabled_domains="aviation")
    assert list_enabled_domain_ids(settings) == ["aviation"]
    assert list_adapter_ids(settings) == ["opensky_flights"]


def test_enabled_domains_loads_both_adapters():
    settings = Settings(enabled_domains="aviation,earthquakes")
    assert set(list_adapter_ids(settings)) == {
        "opensky_flights",
        "usgs_earthquakes",
    }
    registry = get_adapter_registry(settings)
    assert registry["opensky_flights"].adapter_id == "opensky_flights"
    assert registry["usgs_earthquakes"].adapter_id == "usgs_earthquakes"


def test_unknown_domain_raises():
    settings = Settings(enabled_domains="aviation,not_a_domain")
    with pytest.raises(ValueError, match="Unknown domain"):
        list_enabled_domain_ids(settings)


def test_parsed_enabled_domains_normalizes():
    settings = Settings(enabled_domains=" Aviation , Earthquakes ")
    assert settings.parsed_enabled_domains == ["aviation", "earthquakes"]


def test_resolve_solver_defaults_to_stub():
    settings = Settings(enabled_domains="aviation")
    solver = resolve_solver(settings)
    assert isinstance(solver, StubSolver)
