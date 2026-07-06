"""Domain-agnostic solver interface.

This module owns the data contracts that cross the Stage-1 → Stage-2 boundary
and the Stage-2 → Stage-3 boundary of the reasoning pipeline.

Design rule: NOTHING in this module may contain domain-specific entity names
(no Port, no Machine, no Flight).  Domain semantics travel in
``EntityState.attributes`` (a JSONB-sourced dict) and ``ResponseOption.label``
strings chosen by the adapter layer — never in field names or class names here.

Slot for future work
---------------------
``Solver.solve`` is the exact seam where OR-Tools / a discrete-event engine
plugs in per domain.  The swap is configuration-only — no other file in
``/src/core``, ``/src/reasoning``, or ``/src/api`` changes when you replace
``StubSolver`` with a real implementation.  See ``src/solver/`` for the stub
and for future domain-specific implementations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class EntityState:
    """Live-store snapshot for one entity, sourced from the PostGIS tables.

    Used by the solver to quantify the *current* state of affected entities.
    All domain-specific data travels in ``attributes``.
    """

    entity_id: str
    status: str
    attributes: dict[str, Any] = field(default_factory=dict)


#: Mapping from entity_id to its current live-state snapshot.
#: Passed to ``Solver.solve`` so it can quantify the impact delta.
LiveState = dict[str, EntityState]


@dataclass
class AffectedSubgraph:
    """The dependency subgraph produced by Stage-1 structural traversal.

    ``affected_entity_ids``  — every entity reachable from the event via
                               AFFECTED_BY + dependency edges.
    ``dependency_edges``     — directed edges (from_id, to_id, edge_type)
                               within the affected subgraph.  Used by the
                               solver to find longest dependency chains and
                               critical paths.
    ``entity_attributes``    — graph-node properties for each affected entity
                               (callsign, route, origin, type, etc.) keyed by
                               entity_id.  Populated by Stage-1 and forwarded
                               to Stage-3 so the LLM has domain context.
    """

    event_id: str
    scenario_id: str
    affected_entity_ids: list[str]
    dependency_edges: list[tuple[str, str, str]] = field(default_factory=list)
    entity_attributes: dict[str, dict[str, Any]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass
class ResponseOption:
    """One ranked mitigation / response option produced by the solver.

    ``rank``                       — 1 = highest priority.
    ``label``                      — short machine-readable key (no spaces).
    ``description``                — human-readable description for Stage-3.
    ``estimated_impact_reduction`` — fraction 0.0–1.0; how much of the
                                     computed impact this option removes.
    """

    rank: int
    label: str
    description: str
    estimated_impact_reduction: float  # 0.0 – 1.0


@dataclass
class SolverResult:
    """Structured output of Stage-2 quantitative solving.

    Fields are intentionally domain-agnostic: numbers and labeled options,
    no domain semantics.  The Stage-3 LLM receives this alongside the
    affected-subgraph and vector context to produce a grounded explanation.

    ``affected_count``    — number of entities impacted.
    ``max_chain_length``  — depth of the longest dependency chain within
                            the affected subgraph (0 = no edges).
    ``impact_score``      — dimensionless severity 0.0–∞ (stub: bounded 0–1).
    ``response_options``  — ranked list of mitigation options.
    ``explanation``       — plain-English summary of what the solver computed;
                            intended for the Stage-3 prompt, not for end-users.
    ``metadata``          — solver-specific extras (algorithm, timing, etc.).
    """

    event_id: str
    affected_count: int
    max_chain_length: int
    impact_score: float
    response_options: list[ResponseOption] = field(default_factory=list)
    explanation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Solver(Protocol):
    """Pluggable Stage-2 quantitative solver.

    ``solve`` is synchronous because OR-Tools / discrete-event solvers are
    CPU-bound, not I/O-bound.  The async reasoning pipeline wraps it in
    ``asyncio.to_thread`` if needed.

    To add a domain-specific solver:
      1. Create ``src/solver/<domain>.py`` implementing this Protocol.
      2. Wire it into ``src/api/app.py`` (or a dependency injection factory)
         via config — no changes to core, reasoning, or API routes needed.
    """

    def solve(
        self,
        subgraph: AffectedSubgraph,
        live_state: LiveState,
    ) -> SolverResult:
        """Quantify the impact of the simulation event.

        ``subgraph``    — the Stage-1 structural output (affected entities +
                          their dependency edges).
        ``live_state``  — current live-store snapshots for those entities.

        Returns a ``SolverResult`` with numbers and ranked options.
        The LLM must use only these numbers in its response — it must NOT
        invent impact figures beyond what this method returns.
        """
        ...
