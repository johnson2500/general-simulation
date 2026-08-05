# Adding a New Domain to the Simulation Platform

This guide walks through every file you need to create or update to support a
new real-world domain — such as aviation, supply chain, power grids, or
manufacturing — without changing core platform logic.

> **Core design rule:** `src/core`, `src/reasoning`, `src/graph`, and
> `src/solver` (aside from the generic `StubSolver`) contain **zero
> domain-specific names**. A new domain is a package under `domain/<name>/`
> plus one catalog entry. Loading is controlled by `ENABLED_DOMAINS`.

Using Cursor? Paste the staged prompts in
[docs/prompts/add-domain/README.md](docs/prompts/add-domain/README.md)
(Prompt 0 → 1 → …) and keep this file as the human checklist.

---

## Layout at a glance

```
domain/
  aviation/
    adapters/
      opensky_flights.py      # one file per data source
    solver.py                 # optional domain Solver
  earthquakes/
    adapters/
      usgs_earthquakes.py
src/
  ingestion/
    registry.py               # DOMAIN_CATALOG + ENABLED_DOMAINS filtering
    runner.py                 # shared upsert loop (do not put domain logic here)
    tool.py                   # builds tool schema from the registry
  solver/
    stub.py                   # generic fallback when no domain solver is set
```

One **domain** can own **multiple adapters** (e.g. OpenSky positions + a
separate finance feed). Cron / `--adapter` chooses which adapter runs; 
`ENABLED_DOMAINS` chooses which domain packages are loaded.

---

## What "adding a domain" means

| Concern | Where it lives | Changes for a new domain? |
|---|---|---|
| Live ground-truth data | PostGIS (`entity` + `entity_state`) | No — tables are generic |
| Dependency graph | Neo4j | No — nodes and edges are generic |
| Vector / RAG knowledge | pgvector | No — scoped by scenario ID |
| Reasoning pipeline | `src/reasoning/` | No |
| **Domain package** | `domain/<name>/` | **Yes — new package** |
| **Catalog entry** | `src/ingestion/registry.py` | **Yes — add a `DomainSpec`** |
| **Enable at runtime** | `ENABLED_DOMAINS` env / Helm `enabledDomains` | **Yes** |
| **Dependency graph wiring** | Scripts or tests | **Yes — domain bootstrap as needed** |
| **Ingestion CronJob** | Helm / OpenShift | **Yes — `adapterId` + `enabledDomains`** |

---

## Step-by-step walkthrough

We'll use **aviation** (ADS-B flight data from the OpenSky Network) as the
running example. The live adapter already lives at
`domain/aviation/adapters/opensky_flights.py`.

---

### Step 1 — Create the domain package and adapter

```
domain/<your_domain>/
  __init__.py
  adapters/
    __init__.py
    <adapter_id>.py
```

Example path for OpenSky:

```
domain/aviation/adapters/opensky_flights.py
```

Every adapter must satisfy the `IngestionAdapter` protocol in
`src/core/ingestion.py`:

```python
class IngestionAdapter(Protocol):
    @property
    def adapter_id(self) -> str: ...        # stable string key
    async def fetch(self) -> Any: ...        # I/O only, returns raw data
    def normalize(self, raw: Any) -> list[CanonicalEntity]: ...  # pure transform
```

#### `CanonicalEntity` — the only schema you need

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Globally unique entity identifier |
| `type` | `str` | Generic label — e.g. `"moving_entity"`, `"fixed_node"` |
| `timestamp` | `datetime` | UTC observation time |
| `status` | `str` | Current state string (domain-defined values are fine) |
| `geometry` | `dict \| None` | GeoJSON Point/Polygon, or `None` |
| `attributes` | `dict` | All domain-specific fields go here as a JSONB blob |

Do **not** add new columns to the `entity` table for domain fields. Put
everything domain-specific in `attributes`.

#### Example adapter (sketch)

```python
# domain/aviation/adapters/opensky_flights.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.ingestion import CanonicalEntity

OPENSKY_URL = "https://opensky-network.org/api/states/all"
ENTITY_TYPE = "moving_entity"


class OpenSkyFlightsAdapter:
    adapter_id: str = "opensky_flights"

    def __init__(self, api_url: str = OPENSKY_URL, timeout_seconds: float = 20.0) -> None:
        self._api_url = api_url
        self._timeout = timeout_seconds

    async def fetch(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._api_url)
            resp.raise_for_status()
            return resp.json()

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEntity]:
        entities: list[CanonicalEntity] = []
        for state in raw.get("states") or []:
            entity = self._state_to_entity(state)
            if entity is not None:
                entities.append(entity)
        return entities

    @staticmethod
    def _state_to_entity(state: list) -> CanonicalEntity | None:
        icao24 = state[0]
        lon, lat = state[5], state[6]
        if not icao24 or lon is None or lat is None:
            return None
        on_ground = bool(state[8])
        last_contact = state[4]
        return CanonicalEntity(
            id=f"opensky-{icao24}",
            type=ENTITY_TYPE,
            geometry={"type": "Point", "coordinates": [lon, lat]},
            timestamp=datetime.fromtimestamp(last_contact or 0, tz=timezone.utc),
            status="on_ground" if on_ground else "airborne",
            attributes={
                k: v
                for k, v in {
                    "call_sign": (state[1] or "").strip() or None,
                    "origin_country": state[2],
                    "velocity_ms": state[9],
                }.items()
                if v is not None
            },
        )
```

See the real implementation under `domain/aviation/adapters/opensky_flights.py`
for the full field mapping.

---

### Step 2 — Register the domain in the catalog

There is a **single** registry: `src/ingestion/registry.py`. CLI
(`ingest-run`), the LLM ingestion tool, and solver selection all read from it.

Add a `DomainSpec` (or extend an existing domain with another adapter):

```python
# src/ingestion/registry.py

DOMAIN_CATALOG: dict[str, DomainSpec] = {
    # ...
    "aviation": DomainSpec(
        domain_id="aviation",
        adapters={
            "opensky_flights": (
                "domain.aviation.adapters.opensky_flights:OpenSkyFlightsAdapter"
            ),
            # "flight_finance": (
            #     "domain.aviation.adapters.flight_finance:FlightFinanceAdapter"
            # ),
        },
        # solver="domain.aviation.solver:FlightDelaySolver",  # optional
    ),
}
```

You do **not** edit `src/ingestion/__main__.py` or hardcode adapters in
`src/ingestion/tool.py` — those consume the registry.

#### Enable the domain at runtime

```bash
# .env or process environment
ENABLED_DOMAINS=aviation
# or multiple:
ENABLED_DOMAINS=aviation,earthquakes
```

Helm:

```yaml
# deploy/helm/api/values.yaml and deploy/helm/ingestion/values.yaml
enabledDomains: aviation
adapterId: opensky_flights   # which adapter this CronJob runs
```

Then:

```bash
uv run ingest-run --adapter opensky_flights
```

If `--adapter` names an adapter whose domain is not in `ENABLED_DOMAINS`,
the run fails with a clear error.

---

### Step 3 — Write a fixture and a test

Tests must run without a live network connection. Record a small fixture
under `tests/fixtures/<adapter_id>.json` (trim to a few records; include at
least one row that `normalize` should skip).

The shipped reference is the USGS adapter:

```
tests/fixtures/usgs_earthquakes.json
```

Normalize / shape coverage lives in `tests/test_ingestion.py` (imports
`USGSEarthquakeAdapter` and loads that fixture). For a new domain, either
extend that file or add a dedicated `tests/test_<adapter_id>.py` — both are
fine; dedicated files keep larger domains easier to navigate.

```python
# tests/test_<adapter_id>.py  (sketch — mirror tests/test_ingestion.py)
import json
from pathlib import Path

from domain.<name>.adapters.<adapter_id> import YourAdapter

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "<adapter_id>.json").read_text()
)

def test_normalize_returns_canonical_entities():
    entities = YourAdapter().normalize(FIXTURE)
    assert len(entities) >= 1
    assert all(e.id.startswith("<prefix>-") for e in entities)
```

```bash
uv run pytest tests/test_ingestion.py tests/test_registry.py -v
# or: uv run pytest tests/test_<adapter_id>.py tests/test_registry.py -v
```

Registry behaviour (enabled vs disabled domains) is covered in
`tests/test_registry.py`. Neither shipped domain currently has a dedicated
OpenSky fixture file — follow the USGS pattern when adding one.

---

### Step 4 — Wire the dependency graph (optional but recommended)

The dependency graph (Neo4j) captures which entities depend on which.
Wire it once at setup time (script or Job), not inside the adapter. Use
helpers in `src/graph/nodes.py`:

```python
import asyncio
from src.core.config import Settings
from src.core.db import create_neo4j_driver
from src.graph.nodes import create_entity_node, create_dependency_edge

async def bootstrap_flight_graph():
    settings = Settings()
    driver = create_neo4j_driver(settings)
    try:
        await create_entity_node(
            driver, "airport-ORD", "fixed_node", {"name": "O'Hare International"}
        )
        await create_entity_node(
            driver, "atc-chicago", "fixed_node", {"name": "Chicago ARTCC"}
        )
        await create_dependency_edge(
            driver, "airport-ORD", "atc-chicago", edge_type="DEPENDS_ON"
        )
    finally:
        await driver.close()

asyncio.run(bootstrap_flight_graph())
```

After ingestion, live entities appear in Postgres; add edges from those ids to
infrastructure nodes as needed. Stage 1 traversal picks them up automatically.

---

### Step 5 — Write a domain solver (optional)

`StubSolver` already provides generic impact numbers. Add a domain solver only
when you need domain-specific maths (delay propagation, OR-Tools, etc.).

```
domain/aviation/solver.py
```

```python
# domain/aviation/solver.py
from src.core.solver import AffectedSubgraph, LiveState, ResponseOption, SolverResult


class FlightDelaySolver:
    """Domain-specific Stage-2 solver — drop-in Solver protocol implementation."""

    def solve(
        self,
        subgraph: AffectedSubgraph,
        live_state: LiveState,
    ) -> SolverResult:
        total_delay_min = 0
        for eid in subgraph.affected_entity_ids:
            state = live_state.get(eid)
            if state and state.status == "airborne":
                total_delay_min += state.attributes.get("estimated_delay_min", 15)

        impact = min(1.0, total_delay_min / 600)
        return SolverResult(
            event_id=subgraph.event_id,
            affected_count=len(subgraph.affected_entity_ids),
            max_chain_length=0,
            impact_score=round(impact, 4),
            response_options=[
                ResponseOption(
                    rank=1,
                    label="ground_stop",
                    description="Issue a ground stop for departures from the affected airport.",
                    estimated_impact_reduction=0.70,
                ),
            ],
            explanation=(
                f"FlightDelaySolver: {len(subgraph.affected_entity_ids)} aircraft, "
                f"{total_delay_min} min delay, impact {impact:.3f}."
            ),
            metadata={"solver": "flight_delay", "total_delay_min": total_delay_min},
        )
```

Point the catalog at it:

```python
DomainSpec(
    domain_id="aviation",
    adapters={...},
    solver="domain.aviation.solver:FlightDelaySolver",
)
```

`src/api/app.py` calls `resolve_solver(settings)` at startup:

- exactly one enabled domain declares a solver → that solver is used
- none (or more than one) → `StubSolver`

No hard-coded swap in `app.py` is required.

---

### Step 6 — Deploy / schedule ingestion

Set `ENABLED_DOMAINS` (or Helm `enabledDomains`) so the domain package loads,
and set the CronJob `adapterId` to the adapter this job should run:

```yaml
# deploy/helm/ingestion/values.yaml
enabledDomains: aviation
adapterId: opensky_flights
```

For a second adapter on a different schedule, add another CronJob (or Helm
release) with the same `enabledDomains` and a different `adapterId`.

OpenShift ConfigMap (`deploy/openshift/shared/configmaps.yaml`) also carries
`ENABLED_DOMAINS`.

---

## Summary checklist

```
New domain = these files only:

  CREATE  domain/<name>/__init__.py
  CREATE  domain/<name>/adapters/__init__.py
  CREATE  domain/<name>/adapters/<adapter_id>.py
  CREATE  tests/fixtures/<adapter_id>.json            ← recorded API fixture
  CREATE  tests/test_<adapter_id>.py                  ← or extend test_ingestion.py
  UPDATE  src/ingestion/registry.py                   ← DomainSpec (+ adapters)
  UPDATE  tests/test_registry.py                      ← assert catalog entry
  SET     ENABLED_DOMAINS=<name>                      ← .env / Helm / ConfigMap
  CREATE  domain/<name>/solver.py                     ← (optional) real solver
  UPDATE  deploy/helm/ingestion/values.yaml           ← adapterId + enabledDomains
```

Cursor paste-prompts for the same checklist:
[docs/prompts/add-domain/README.md](docs/prompts/add-domain/README.md).

**Files you normally never touch for domain logic:**

```
  src/core/           ← domain-agnostic interfaces and settings
  src/reasoning/      ← ReAct pipeline
  src/graph/          ← Neo4j helpers
  src/llm/            ← inference + pgvector RAG
  src/api/query.py    ← POST /query route
  src/ingestion/runner.py
  src/ingestion/tool.py   ← schema built from registry
  src/ingestion/__main__.py
```

---

## Common pitfalls

| Pitfall | Fix |
|---|---|
| Adding a domain-specific field to `CanonicalEntity` | Put it in `attributes` (JSONB) instead |
| Adding a new Postgres column for domain data | Don't — use `attributes` |
| Editing `__main__.py` / `tool.py` to hardcode adapters | Register in `DOMAIN_CATALOG` only |
| Forgetting `ENABLED_DOMAINS` | Adapter won't load; CLI/tool will reject it |
| Two adapters writing the same entity `id` | Upsert **replaces** `attributes` — merge keys or re-emit full attributes |
| Calling the inference API from an adapter | Don't — LLM/vector calls go through `src/llm/` |
| Mutating the live store during a simulation query | Don't — overlays are additive; live rows are ingestion-only |
| Hardcoding a domain entity name in a core module | Keep it in `domain/` or `attributes` |
| Forgetting `--enable-auto-tool-choice` on vLLM | Tool calls from the reasoning pipeline will fail silently |
