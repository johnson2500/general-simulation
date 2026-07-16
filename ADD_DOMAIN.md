# Adding a New Domain to the Simulation Platform

This guide walks through every file you need to create or update to support a
new real-world domain — such as supply chain, air traffic, power grids, or
manufacturing — without changing any core platform code.

> **Core design rule:** `/src/core`, `/src/reasoning`, `/src/graph`, and
> `/src/solver` contain **zero domain-specific names**. A new domain means new
> files in the adapter and solver layers only. Nothing else changes.

---

## What "adding a domain" means

The platform separates three concerns:

| Concern | Where it lives | Changes for a new domain? |
|---|---|---|
| Live ground-truth data | PostGIS (`entity` + `entity_state` tables) | No — tables are generic |
| Dependency graph | Neo4j (Cypher via async driver) | No — nodes and edges are generic |
| Vector/RAG knowledge | Llama Stack pgvector store | No — scoped by scenario ID |
| Reasoning pipeline | `src/reasoning/` | No |
| **Data ingestion** | `src/ingestion/adapters/` | **Yes — new adapter file** |
| **Quantitative solver** | `src/solver/` | **Yes — new solver file (optional)** |
| **Dependency graph wiring** | Caller code (scripts or tests) | **Yes — new graph bootstrap** |
| **OpenShift CronJob** | `deploy/openshift/ingestion/` | **Yes — update or add CronJob** |

---

## Step-by-step walkthrough

We'll use **air traffic** (ADS-B flight data from the OpenSky Network) as a
running example throughout.

---

### Step 1 — Write the ingestion adapter

Create a new file under `src/ingestion/adapters/`:

```
src/ingestion/adapters/opensky_flights.py
```

Every adapter must satisfy the `IngestionAdapter` protocol defined in
`src/core/ingestion.py`. That protocol requires three things:

```python
class IngestionAdapter(Protocol):
    @property
    def adapter_id(self) -> str: ...        # stable string key
    async def fetch(self) -> Any: ...        # I/O only, returns raw data
    def normalize(self, raw: Any) -> list[CanonicalEntity]: ...  # pure transform
```

#### `CanonicalEntity` — the only schema you need

All adapters produce `CanonicalEntity` records. The fields are intentionally
generic:

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Globally unique entity identifier |
| `type` | `str` | Generic label — e.g. `"moving_entity"`, `"fixed_node"` |
| `timestamp` | `datetime` | UTC observation time |
| `status` | `str` | Current state string (domain-defined values are fine here) |
| `geometry` | `dict \| None` | GeoJSON Point/Polygon, or `None` |
| `attributes` | `dict` | All domain-specific fields go here as a JSONB blob |

Do **not** add new columns to the `entity` table for domain fields. Put
everything domain-specific in `attributes`.

#### Example adapter

```python
# src/ingestion/adapters/opensky_flights.py
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from src.core.ingestion import CanonicalEntity

logger = logging.getLogger(__name__)

OPENSKY_URL = "https://opensky-network.org/api/states/all"
ENTITY_TYPE = "moving_entity"   # keep generic


class OpenSkyFlightAdapter:
    """Fetches live ADS-B flight states from the OpenSky Network REST API.

    Each aircraft state vector maps to one CanonicalEntity:
      id         — ICAO 24-bit address (prefixed "flight-")
      type       — "moving_entity"
      geometry   — GeoJSON Point [lon, lat, altitude_m]
      timestamp  — last contact time (UTC)
      status     — "airborne" | "grounded"
      attributes — callsign, velocity_ms, heading, origin_country, …
    """

    adapter_id: str = "opensky_flights"

    def __init__(
        self,
        api_url: str = OPENSKY_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        self._api_url = api_url
        self._timeout = timeout_seconds

    async def fetch(self) -> dict[str, Any]:
        """GET the OpenSky states/all endpoint."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(self._api_url)
            resp.raise_for_status()
            return resp.json()

    def normalize(self, raw: dict[str, Any]) -> list[CanonicalEntity]:
        """Convert OpenSky state vectors to CanonicalEntity records."""
        states: list[list] = raw.get("states") or []
        entities: list[CanonicalEntity] = []
        for state in states:
            entity = self._state_to_entity(state)
            if entity is not None:
                entities.append(entity)
        logger.debug("OpenSky normalise: %d → %d entities", len(states), len(entities))
        return entities

    @staticmethod
    def _state_to_entity(state: list) -> CanonicalEntity | None:
        """Map one OpenSky state vector to a CanonicalEntity."""
        try:
            icao24       = state[0]   # ICAO 24-bit address
            callsign     = (state[1] or "").strip()
            origin_country = state[2]
            last_contact = state[4]   # Unix timestamp (seconds)
            lon          = state[5]   # decimal degrees
            lat          = state[6]
            altitude_m   = state[7]   # barometric altitude in metres
            on_ground    = state[8]
            velocity_ms  = state[9]
            heading      = state[10]

            if not icao24 or lon is None or lat is None:
                return None

            timestamp = datetime.fromtimestamp(
                last_contact or 0, tz=timezone.utc
            )

            geometry = {
                "type": "Point",
                "coordinates": [lon, lat, altitude_m or 0],
            }

            return CanonicalEntity(
                id=f"flight-{icao24}",
                type=ENTITY_TYPE,
                geometry=geometry,
                timestamp=timestamp,
                status="grounded" if on_ground else "airborne",
                attributes={
                    k: v for k, v in {
                        "callsign": callsign or None,
                        "origin_country": origin_country,
                        "altitude_m": altitude_m,
                        "velocity_ms": velocity_ms,
                        "heading": heading,
                    }.items() if v is not None
                },
            )
        except Exception:
            logger.exception("Error normalising OpenSky state: %s", state)
            return None
```

---

### Step 2 — Register the adapter in three places

#### 2a. CLI entry point — `src/ingestion/__main__.py`

Add an import and entry to the `_ADAPTERS` dict so `uv run ingest-run` can use it:

```python
# src/ingestion/__main__.py  (existing file — add the highlighted lines)

from src.ingestion.adapters.usgs_earthquakes import USGSEarthquakeAdapter
from src.ingestion.adapters.opensky_flights import OpenSkyFlightAdapter  # ADD

_ADAPTERS = {
    "usgs_earthquakes": USGSEarthquakeAdapter,
    "opensky_flights": OpenSkyFlightAdapter,                              # ADD
}
```

#### 2b. Llama Stack tool registry — `src/ingestion/tool.py`

Add the adapter to `_ADAPTER_REGISTRY` and update the `enum` in the tool schema
so the LLM knows it can request an on-demand pull:

```python
# src/ingestion/tool.py  (existing file — add the highlighted lines)

from src.ingestion.adapters.usgs_earthquakes import USGSEarthquakeAdapter
from src.ingestion.adapters.opensky_flights import OpenSkyFlightAdapter  # ADD

# In INGESTION_TOOL_SCHEMA → parameters → adapter_id → enum:
"enum": ["usgs_earthquakes", "opensky_flights"],                         # ADD

# In _ADAPTER_REGISTRY:
_ADAPTER_REGISTRY: dict[str, Any] = {
    "usgs_earthquakes": USGSEarthquakeAdapter,
    "opensky_flights": OpenSkyFlightAdapter,                             # ADD
}
```

---

### Step 3 — Write a fixture and a test

Tests must run without a live network connection. Record a small fixture file
that mirrors the API response shape:

```
tests/fixtures/opensky_flights.json
```

```json
{
  "time": 1700000000,
  "states": [
    ["a1b2c3", "AAL123  ", "United States", 1700000000, 1700000000,
     -87.6298, 41.8781, 10060.0, false, 230.5, 270.0, null, null, null, "1200", false, 0],
    ["d4e5f6", "UAL456  ", "United States", 1700000000, 1700000000,
     -73.7789, 40.6413, null, true, 0.0, null, null, null, null, "2000", false, 0]
  ]
}
```

```python
# tests/test_opensky.py
import json
from pathlib import Path
from src.ingestion.adapters.opensky_flights import OpenSkyFlightAdapter

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "opensky_flights.json").read_text()
)

def test_normalize_returns_canonical_entities():
    adapter = OpenSkyFlightAdapter()
    entities = adapter.normalize(FIXTURE)
    assert len(entities) == 2

def test_airborne_status():
    adapter = OpenSkyFlightAdapter()
    entities = adapter.normalize(FIXTURE)
    airborne = [e for e in entities if e.status == "airborne"]
    grounded = [e for e in entities if e.status == "grounded"]
    assert len(airborne) == 1
    assert len(grounded) == 1

def test_id_prefix():
    adapter = OpenSkyFlightAdapter()
    entities = adapter.normalize(FIXTURE)
    assert all(e.id.startswith("flight-") for e in entities)

def test_grounded_has_no_altitude():
    adapter = OpenSkyFlightAdapter()
    entities = adapter.normalize(FIXTURE)
    grounded = next(e for e in entities if e.status == "grounded")
    # altitude_m should be absent when None (we drop None values)
    assert "altitude_m" not in grounded.attributes
```

Run with:
```bash
uv run pytest tests/test_opensky.py -v
```

---

### Step 4 — Wire the dependency graph (optional but recommended)

The dependency graph (Neo4j) captures which entities depend on which.
For air traffic this might be: flights depend on the airport they're departing
from, airports depend on the air traffic control (ATC) facilities serving them.

You wire this graph once at setup time (a one-off script or an OpenShift Job),
not inside the adapter. Use the helpers in `src/graph/nodes.py`, which accept a
Neo4j `AsyncDriver` (created by `create_neo4j_driver`):

```python
# Example setup script (run once, not part of the adapter)
import asyncio
from src.core.config import Settings
from src.core.db import create_neo4j_driver
from src.graph.nodes import create_entity_node, create_dependency_edge

async def bootstrap_flight_graph():
    settings = Settings()
    driver = create_neo4j_driver(settings)
    try:
        # Create fixed infrastructure nodes
        await create_entity_node(driver, "airport-ORD", "fixed_node",
                                 {"name": "O'Hare International"})
        await create_entity_node(driver, "atc-chicago", "fixed_node",
                                 {"name": "Chicago ARTCC"})

        # Airports depend on their ATC facility
        await create_dependency_edge(driver, "airport-ORD", "atc-chicago",
                                     edge_type="DEPENDS_ON")
    finally:
        await driver.close()

asyncio.run(bootstrap_flight_graph())
```

After ingestion runs, individual flights (e.g. `flight-a1b2c3`) will appear in
the `entity` table. You can then add edges from flights to airports:

```python
await create_dependency_edge(driver, "flight-a1b2c3", "airport-ORD",
                             edge_type="DEPENDS_ON")
```

When a `SimulationEvent` (e.g. "ORD runway closure") is injected, Stage 1 of
the reasoning pipeline will automatically traverse these edges to find all
affected downstream entities — no changes to the pipeline code required.

---

### Step 5 — Write a domain solver (optional)

The `StubSolver` already runs and provides generic impact numbers for any
domain. You only need a custom solver when you want domain-specific
calculations — for example, computing flight delay propagation using a
discrete-event model, or running an OR-Tools optimisation.

Create a new file:

```
src/solver/flight_delay.py
```

```python
# src/solver/flight_delay.py
from src.core.solver import AffectedSubgraph, LiveState, ResponseOption, SolverResult

class FlightDelaySolver:
    """Domain-specific solver: estimates total delay minutes for a disruption.

    Implements the Solver Protocol — drop-in replacement for StubSolver.
    """

    def solve(
        self,
        subgraph: AffectedSubgraph,
        live_state: LiveState,
    ) -> SolverResult:
        # Example: sum estimated delay from each affected entity's attributes
        total_delay_min = 0
        for eid in subgraph.affected_entity_ids:
            state = live_state.get(eid)
            if state and state.status == "airborne":
                # Domain-specific attribute added during ingestion
                total_delay_min += state.attributes.get("estimated_delay_min", 15)

        impact = min(1.0, total_delay_min / 600)  # normalise to [0,1]

        return SolverResult(
            event_id=subgraph.event_id,
            affected_count=len(subgraph.affected_entity_ids),
            max_chain_length=0,   # compute if needed
            impact_score=round(impact, 4),
            response_options=[
                ResponseOption(
                    rank=1,
                    label="ground_stop",
                    description="Issue a ground stop for all departures from the affected airport.",
                    estimated_impact_reduction=0.70,
                ),
                ResponseOption(
                    rank=2,
                    label="reroute_en_route",
                    description="Reroute en-route aircraft around the affected sector.",
                    estimated_impact_reduction=0.45,
                ),
            ],
            explanation=(
                f"FlightDelaySolver: {len(subgraph.affected_entity_ids)} aircraft affected, "
                f"estimated total delay {total_delay_min} minutes, "
                f"impact score {impact:.3f}."
            ),
            metadata={"solver": "flight_delay", "total_delay_min": total_delay_min},
        )
```

#### Inject the new solver into the app

In `src/api/app.py`, swap the solver in the lifespan:

```python
# src/api/app.py  (existing file — change one line in the lifespan)

from src.solver.flight_delay import FlightDelaySolver   # ADD

@asynccontextmanager
async def lifespan(app: FastAPI):
    ...
    app.state.solver = FlightDelaySolver()   # was: StubSolver()
    ...
```

Nothing else changes. The reasoning pipeline (`src/reasoning/`) picks up the
new solver automatically via FastAPI dependency injection.

---

### Step 6 — Update the OpenShift CronJob (if deploying)

If you want the new adapter to run on a schedule in OpenShift, either:

**Option A** — Update the existing CronJob command to add `--adapter opensky_flights`:

```yaml
# deploy/openshift/ingestion/cronjob.yaml
command: ["ingest-run"]
args: ["--adapter", "opensky_flights"]
```

**Option B** — Create a second CronJob for the new adapter (better if the two
adapters have different schedules):

```bash
cp deploy/openshift/ingestion/cronjob.yaml \
   deploy/openshift/ingestion/cronjob-opensky.yaml
# Edit the copy: change the name, schedule, and --adapter arg
```

---

## Summary checklist

```
New domain = these files only:

  CREATE  src/ingestion/adapters/<your_domain>.py      ← new adapter
  CREATE  tests/fixtures/<your_domain>.json            ← recorded API fixture
  CREATE  tests/test_<your_domain>.py                  ← normalize + shape tests
  UPDATE  src/ingestion/__main__.py                    ← add to _ADAPTERS dict
  UPDATE  src/ingestion/tool.py                        ← add to registry + enum
  CREATE  src/solver/<your_domain>.py                  ← (optional) real solver
  UPDATE  src/api/app.py                               ← (optional) swap solver
  UPDATE  deploy/openshift/ingestion/cronjob.yaml      ← (optional) schedule
```

**Files you never touch:**

```
  src/core/           ← domain-agnostic interfaces and settings
  src/reasoning/      ← three-stage pipeline
  src/graph/          ← Neo4j graph helpers (Cypher, nodes, events)
  src/llm/          ← LLM client: OpenAI-compatible inference + direct pgvector RAG
  src/api/query.py    ← POST /query route
  deploy/openshift/   ← (mostly) all other manifests
```

---

## Common pitfalls

| Pitfall | Fix |
|---|---|
| Adding a domain-specific field to `CanonicalEntity` | Put it in `attributes` (JSONB dict) instead |
| Adding a new Postgres column for domain data | Don't — use `attributes`; add an index on the JSONB key if needed |
| Calling the inference API or pgvector SQL directly from the adapter | Don’t — all LLM/vector calls go through `src/llm/` |
| Mutating the live store during a simulation query | Don't — simulation overlays (`inject_event`) are additive and reversible; `entity` and `entity_state` rows are written only by the ingestion runner |
| Hardcoding a domain entity name (e.g. `Flight`, `Port`) in a core module | Move it to `attributes` or a domain adapter; keep core code generic |
| Forgetting `--enable-auto-tool-choice` on vLLM | Tool calls from the reasoning pipeline will silently fail without this flag |
