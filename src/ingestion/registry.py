"""Domain catalog and adapter/solver registry.

Static catalog maps domain ids to importable adapters (and optional solvers).
``Settings.enabled_domains`` controls which domains are loaded at runtime.
CLI, ingestion tool, and API solver wiring all go through this module.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import Any

from src.core.config import Settings
from src.core.solver import Solver

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DomainSpec:
    """Static description of one domain package."""

    domain_id: str
    #: adapter_id → "module.path:ClassName"
    adapters: dict[str, str] = field(default_factory=dict)
    #: Optional "module.path:ClassName" implementing Solver.  None → StubSolver.
    solver: str | None = None


# ---------------------------------------------------------------------------
# Catalog — add a DomainSpec entry when introducing a new domain package.
# ---------------------------------------------------------------------------

DOMAIN_CATALOG: dict[str, DomainSpec] = {
    "aviation": DomainSpec(
        domain_id="aviation",
        adapters={
            "opensky_flights": (
                "domain.aviation.adapters.opensky_flights:OpenSkyFlightsAdapter"
            ),
        },
    ),
    "shipping": DomainSpec(
        domain_id="shipping",
        adapters={
            "shipping_demo": (
                "domain.shipping.adapters.shipping_demo:ShippingDemoAdapter"
            ),
        },
    ),
}


def _import_symbol(path: str) -> Any:
    """Import ``module.path:ClassName`` and return the attribute."""
    module_path, _, attr = path.partition(":")
    if not module_path or not attr:
        raise ValueError(f"Invalid import path '{path}' (expected module:Class)")
    module = importlib.import_module(module_path)
    return getattr(module, attr)


def _validate_enabled_domains(domain_ids: list[str]) -> list[str]:
    unknown = [d for d in domain_ids if d not in DOMAIN_CATALOG]
    if unknown:
        known = ", ".join(sorted(DOMAIN_CATALOG))
        raise ValueError(
            f"Unknown domain(s) in ENABLED_DOMAINS: {unknown}. "
            f"Known domains: {known}"
        )
    return domain_ids


def list_enabled_domain_ids(settings: Settings | None = None) -> list[str]:
    """Return validated enabled domain ids from settings."""
    settings = settings or Settings()
    return _validate_enabled_domains(settings.parsed_enabled_domains)


def get_adapter_registry(
    settings: Settings | None = None,
) -> dict[str, type]:
    """Load adapter classes for all enabled domains.

    Returns a mapping of adapter_id → adapter class.
    """
    settings = settings or Settings()
    registry: dict[str, type] = {}
    for domain_id in list_enabled_domain_ids(settings):
        spec = DOMAIN_CATALOG[domain_id]
        for adapter_id, import_path in spec.adapters.items():
            if adapter_id in registry:
                raise ValueError(
                    f"Duplicate adapter_id '{adapter_id}' across enabled domains"
                )
            registry[adapter_id] = _import_symbol(import_path)
    return registry


def list_adapter_ids(settings: Settings | None = None) -> list[str]:
    """Sorted adapter ids available under the current ENABLED_DOMAINS."""
    return sorted(get_adapter_registry(settings))


def get_adapter_class(
    adapter_id: str,
    settings: Settings | None = None,
) -> type:
    """Return the adapter class for *adapter_id*, or raise KeyError."""
    registry = get_adapter_registry(settings)
    if adapter_id not in registry:
        available = list(registry) or ["(none — check ENABLED_DOMAINS)"]
        raise KeyError(
            f"Adapter '{adapter_id}' is not available. "
            f"Available: {available}"
        )
    return registry[adapter_id]


def resolve_solver(settings: Settings | None = None) -> Solver:
    """Pick a Stage-2 solver from enabled domains.

    - Exactly one enabled domain declares a solver → instantiate it.
    - Zero or multiple domain solvers → StubSolver (with a warning if multiple).
    """
    from src.solver.stub import StubSolver

    settings = settings or Settings()
    loaded: list[tuple[str, Solver]] = []
    for domain_id in list_enabled_domain_ids(settings):
        spec = DOMAIN_CATALOG[domain_id]
        if not spec.solver:
            continue
        cls = _import_symbol(spec.solver)
        loaded.append((domain_id, cls()))

    if len(loaded) == 1:
        domain_id, solver = loaded[0]
        logger.info("Using domain solver from '%s': %s", domain_id, type(solver).__name__)
        return solver

    if len(loaded) > 1:
        names = [d for d, _ in loaded]
        logger.warning(
            "Multiple domain solvers enabled (%s); falling back to StubSolver",
            names,
        )

    return StubSolver()
