"""Stub solver — deterministic, explainable placeholder for Stage-2.

Proves the Solver interface and lets the full reasoning pipeline run
end-to-end without any domain-specific OR-Tools implementation.

What it computes (all from the affected subgraph + live state alone):
  1. ``affected_count``       — len(subgraph.affected_entity_ids)
  2. ``max_chain_length``     — length of the longest path through the
                                dependency sub-DAG  (DFS with memoisation)
  3. ``impact_score``         — weighted combination of the above (0.0–1.0)
  4. ``total_value_at_risk``  — sum of economic value on affected entities
  5. ``response_options``     — tiered options calibrated to the impact level

Nothing here is domain-specific.  Replace this with a real OR-Tools or
discrete-event solver by implementing the ``Solver`` Protocol in a new
``src/solver/<domain>.py`` file and injecting it instead.
"""
from __future__ import annotations

import logging
from typing import Any

from src.core.solver import (
    AffectedSubgraph,
    LiveState,
    RecommendedReroute,
    ResponseOption,
    Solver,
    SolverResult,
)

logger = logging.getLogger(__name__)

# Weights used to combine sub-metrics into a single impact score.
_WEIGHT_COUNT = 0.15
_WEIGHT_CHAIN = 0.25
_MAX_SCORE = 1.0

# Impact thresholds for choosing the response tier.
_THRESHOLD_LOW = 0.25
_THRESHOLD_MED = 0.55

_DEFAULT_CURRENCY = "USD"

# Curated European alternates used when response options include rerouting.
# Coordinates are WGS-84; labels are for map popups / Stage-3 grounding.
_ALTERNATE_TARGETS: list[dict[str, Any]] = [
    {
        "id": "EIDW",
        "iata": "DUB",
        "label": "Dublin (EIDW)",
        "lat": 53.4213,
        "lon": -6.2701,
    },
    {
        "id": "LFPG",
        "iata": "CDG",
        "label": "Paris CDG (LFPG)",
        "lat": 49.0097,
        "lon": 2.5479,
    },
    {
        "id": "EHAM",
        "iata": "AMS",
        "label": "Amsterdam (EHAM)",
        "lat": 52.3105,
        "lon": 4.7683,
    },
    {
        "id": "EBBR",
        "iata": "BRU",
        "label": "Brussels (EBBR)",
        "lat": 50.9014,
        "lon": 4.4844,
    },
    {
        "id": "EDDF",
        "iata": "FRA",
        "label": "Frankfurt (EDDF)",
        "lat": 50.0379,
        "lon": 8.5622,
    },
]

_REROUTE_OPTION_LABELS = frozenset(
    {
        "reroute_dependencies",
        "emergency_response",
        "isolate_affected_entities",
    }
)


class StubSolver:
    """Deterministic stub that satisfies the Solver Protocol.

    Output is fully determined by the *structure* of the affected subgraph
    (entity count + dependency topology) plus economic attributes on
    entities.  No randomness, no external calls.

    This intentional simplicity makes it easy to assert exact values in
    tests and easy to reason about in Stage-3 prompts.
    """

    # ── Solver Protocol ───────────────────────────────────────────────────────

    def solve(
        self,
        subgraph: AffectedSubgraph,
        live_state: LiveState,
    ) -> SolverResult:
        affected_count = len(subgraph.affected_entity_ids)
        chain_len = _longest_chain(
            subgraph.dependency_edges,
            set(subgraph.affected_entity_ids),
        )
        impact = _impact_score(affected_count, chain_len)
        options = _response_options(impact)
        value_by_entity = _value_by_entity(subgraph, live_state)
        total_value = round(sum(value_by_entity.values()), 2)
        breakdown = [
            {"entity_id": eid, "value_usd": value}
            for eid, value in sorted(value_by_entity.items())
            if value > 0
        ]
        reroutes = _recommended_reroutes(subgraph, live_state, options)
        explanation = _explanation(
            subgraph,
            live_state,
            affected_count,
            chain_len,
            impact,
            total_value,
            reroutes,
        )

        logger.debug(
            "StubSolver: event=%s count=%d chain=%d score=%.3f value=%.2f reroutes=%d",
            subgraph.event_id,
            affected_count,
            chain_len,
            impact,
            total_value,
            len(reroutes),
        )

        return SolverResult(
            event_id=subgraph.event_id,
            affected_count=affected_count,
            max_chain_length=chain_len,
            impact_score=round(impact, 4),
            total_value_at_risk=total_value,
            currency=_DEFAULT_CURRENCY,
            value_breakdown=breakdown,
            response_options=options,
            recommended_reroutes=reroutes,
            explanation=explanation,
            metadata={
                "solver": "stub",
                "scenario_id": subgraph.scenario_id,
                "edge_count": len(subgraph.dependency_edges),
                "value_by_entity": value_by_entity,
            },
        )


# ---------------------------------------------------------------------------
# Pure helpers (testable independently)
# ---------------------------------------------------------------------------


def _entity_value_usd(attrs: dict[str, Any] | None) -> float:
    """Resolve economic value from entity attributes.

    Precedence:
      1. ``value_usd``
      2. ``revenue_usd``
      3. ``unit_price_usd`` × ``quantity`` (qty defaults to 1)
      4. 0
    """
    if not attrs:
        return 0.0
    if "value_usd" in attrs and attrs["value_usd"] is not None:
        try:
            return float(attrs["value_usd"])
        except (TypeError, ValueError):
            return 0.0
    if "revenue_usd" in attrs and attrs["revenue_usd"] is not None:
        try:
            return float(attrs["revenue_usd"])
        except (TypeError, ValueError):
            return 0.0
    if "unit_price_usd" in attrs and attrs["unit_price_usd"] is not None:
        try:
            unit = float(attrs["unit_price_usd"])
        except (TypeError, ValueError):
            return 0.0
        qty_raw = attrs.get("quantity", 1)
        try:
            qty = float(qty_raw) if qty_raw is not None else 1.0
        except (TypeError, ValueError):
            qty = 1.0
        return unit * qty
    return 0.0


def _attrs_for_entity(
    entity_id: str,
    subgraph: AffectedSubgraph,
    live_state: LiveState,
) -> dict[str, Any]:
    """Prefer live-state attributes; fall back to graph node properties."""
    state = live_state.get(entity_id)
    if state and state.attributes:
        return state.attributes
    return subgraph.entity_attributes.get(entity_id, {})


def _value_by_entity(
    subgraph: AffectedSubgraph,
    live_state: LiveState,
) -> dict[str, float]:
    """Map each affected entity id to its resolved USD value."""
    out: dict[str, float] = {}
    for eid in subgraph.affected_entity_ids:
        value = _entity_value_usd(_attrs_for_entity(eid, subgraph, live_state))
        out[eid] = round(value, 2)
    return out


def _total_value_at_risk(
    subgraph: AffectedSubgraph,
    live_state: LiveState,
) -> float:
    """Sum of economic value across all affected entities."""
    return round(sum(_value_by_entity(subgraph, live_state).values()), 2)


def _longest_chain(
    edges: list[tuple[str, str, str]],
    nodes: set[str],
) -> int:
    """Return the number of *hops* on the longest path through the DAG.

    An isolated node has chain length 0.  A single edge A→B gives length 1.
    Cycles (which should not occur in a dependency graph) are broken by the
    memoisation guard — each node is visited at most once.
    """
    if not edges or not nodes:
        return 0

    # Build forward adjacency list restricted to the affected subgraph nodes.
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for from_id, to_id, _ in edges:
        if from_id in adj and to_id in adj:
            adj[from_id].append(to_id)

    memo: dict[str, int] = {}

    def _dfs(node: str, visiting: frozenset[str]) -> int:
        if node in memo:
            return memo[node]
        successors = [s for s in adj.get(node, []) if s not in visiting]
        if not successors:
            memo[node] = 0
            return 0
        depth = 1 + max(_dfs(s, visiting | {node}) for s in successors)
        memo[node] = depth
        return depth

    return max(_dfs(n, frozenset()) for n in nodes)


def _impact_score(affected_count: int, chain_length: int) -> float:
    """Dimensionless severity score in [0.0, 1.0]."""
    raw = affected_count * _WEIGHT_COUNT + chain_length * _WEIGHT_CHAIN
    return min(_MAX_SCORE, raw)


def _response_options(impact: float) -> list[ResponseOption]:
    """Return a tier of ranked response options based on impact severity."""
    if impact < _THRESHOLD_LOW:
        return [
            ResponseOption(
                rank=1,
                label="monitor_and_log",
                description=(
                    "Impact is low.  Continue normal operations; "
                    "log the event for post-hoc analysis."
                ),
                estimated_impact_reduction=0.0,
            ),
        ]
    if impact < _THRESHOLD_MED:
        return [
            ResponseOption(
                rank=1,
                label="trigger_downstream_alerts",
                description=(
                    "Notify downstream entities of the perturbation so they "
                    "can adjust their own operations preemptively."
                ),
                estimated_impact_reduction=0.35,
            ),
            ResponseOption(
                rank=2,
                label="reroute_dependencies",
                description=(
                    "Temporarily redirect flows through alternative dependency "
                    "paths to bypass affected entities."
                ),
                estimated_impact_reduction=0.60,
            ),
        ]
    # High impact
    return [
        ResponseOption(
            rank=1,
            label="emergency_response",
            description=(
                "Activate emergency protocols across all affected entities. "
                "Escalate to human decision-makers immediately."
            ),
            estimated_impact_reduction=0.80,
        ),
        ResponseOption(
            rank=2,
            label="isolate_affected_entities",
            description=(
                "Isolate affected entities from the wider dependency graph "
                "to prevent cascade propagation."
            ),
            estimated_impact_reduction=0.65,
        ),
        ResponseOption(
            rank=3,
            label="notify_stakeholders",
            description=(
                "Broadcast impact summary to all registered stakeholders "
                "for coordinated response planning."
            ),
            estimated_impact_reduction=0.20,
        ),
    ]


def _is_reroutable_entity(
    entity_id: str,
    attrs: dict[str, Any],
) -> bool:
    """True when the entity looks like a movable asset (not cargo/fixed)."""
    if entity_id.startswith("cargo-"):
        return False
    entity_type = str(attrs.get("type", "")).lower()
    if entity_type in {"cargo_item", "cargo", "fixed_node"}:
        return False
    # Prefer entities that already carry route / callsign attributes.
    return bool(attrs.get("route") or attrs.get("call_sign") or attrs.get("callsign"))


def _route_codes(attrs: dict[str, Any]) -> set[str]:
    """Parse IATA-ish codes from a ``ROUTE`` / ``LHR-JFK`` style attribute."""
    route = attrs.get("route")
    if not isinstance(route, str) or not route.strip():
        return set()
    parts = {p.strip().upper() for p in route.replace("→", "-").split("-") if p.strip()}
    return {p for p in parts if 3 <= len(p) <= 4 and p.isalpha()}


def _pick_alternate(
    attrs: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    """Choose an alternate that is not already an endpoint of the entity route."""
    avoid = _route_codes(attrs)

    def _conflicts(target: dict[str, Any]) -> bool:
        tid = str(target["id"]).upper()
        iata = str(target.get("iata", "")).upper()
        return tid in avoid or (iata != "" and iata in avoid)

    eligible = [t for t in _ALTERNATE_TARGETS if not _conflicts(t)]
    pool = eligible or _ALTERNATE_TARGETS
    return pool[index % len(pool)]


def _recommended_reroutes(
    subgraph: AffectedSubgraph,
    live_state: LiveState,
    options: list[ResponseOption],
) -> list[RecommendedReroute]:
    """Assign alternate targets when response options include rerouting.

    Deterministic: same affected set always yields the same alternates.
    Returns an empty list for low-impact / monitor-only tiers.
    """
    if not any(opt.label in _REROUTE_OPTION_LABELS for opt in options):
        return []

    reroutes: list[RecommendedReroute] = []
    index = 0
    for eid in subgraph.affected_entity_ids:
        attrs = _attrs_for_entity(eid, subgraph, live_state)
        if not _is_reroutable_entity(eid, attrs):
            continue
        target = _pick_alternate(attrs, index)
        callsign = attrs.get("call_sign") or attrs.get("callsign") or eid
        route = attrs.get("route") or "unknown route"
        reroutes.append(
            RecommendedReroute(
                entity_id=eid,
                target_id=str(target["id"]),
                target_label=str(target["label"]),
                latitude=float(target["lat"]),
                longitude=float(target["lon"]),
                rationale=(
                    f"Divert {callsign} ({route}) to {target['label']} "
                    f"while the disruption is active."
                ),
            )
        )
        index += 1
    return reroutes


def _explanation(
    subgraph: AffectedSubgraph,
    live_state: LiveState,
    affected_count: int,
    chain_len: int,
    impact: float,
    total_value: float,
    reroutes: list[RecommendedReroute] | None = None,
) -> str:
    """Build a plain-English explanation for Stage-3 prompt context."""
    status_counts: dict[str, int] = {}
    for eid in subgraph.affected_entity_ids:
        state = live_state.get(eid)
        s = state.status if state else "unknown"
        status_counts[s] = status_counts.get(s, 0) + 1

    status_summary = ", ".join(
        f"{count} '{status}'" for status, count in sorted(status_counts.items())
    )
    chain_note = (
        f"The longest dependency chain through the affected subgraph is "
        f"{chain_len} hop(s)."
        if chain_len > 0
        else "There are no dependency edges within the affected subgraph."
    )

    reroute_note = ""
    if reroutes:
        parts = [
            f"{r.entity_id}→{r.target_id}" for r in reroutes[:8]
        ]
        more = "" if len(reroutes) <= 8 else f" (+{len(reroutes) - 8} more)"
        reroute_note = (
            f" Recommended reroutes: {', '.join(parts)}{more}."
        )

    return (
        f"StubSolver analysis for event '{subgraph.event_id}' "
        f"(scenario '{subgraph.scenario_id}'): "
        f"{affected_count} entit{'y' if affected_count == 1 else 'ies'} affected "
        f"with statuses — {status_summary or 'none recorded'}. "
        f"{chain_note} "
        f"Computed impact score: {impact:.3f} "
        f"(weights: count×{_WEIGHT_COUNT}, chain×{_WEIGHT_CHAIN}). "
        f"Total value at risk: {_DEFAULT_CURRENCY} {total_value:,.2f}."
        f"{reroute_note}"
    )
