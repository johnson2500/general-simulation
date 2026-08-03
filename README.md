# General Simulation & Impact-Reasoning Platform (MVP)

A **domain-agnostic** simulation and impact-reasoning platform built on:

| Concern | Technology |
|---|---|
| Inference & embeddings | OpenAI-compatible endpoint (OpenAI by default; point at vLLM / Llama Stack via `LLM_BASE_URL`) |
| Vector / RAG | pgvector (queried directly via asyncpg) |
| Dependency graph | **Neo4j** (native async driver, Cypher queries) |
| Live / geo snapshot | PostGIS, queried directly |
| API | FastAPI |
| Admin UI | Built-in SPA at `/admin/` |
| Dependency management | [uv](https://docs.astral.sh/uv/) |

> **Core design rule:** Live ground-truth data is **never mutated** by a simulation.
> Simulations are overlays applied at query time.

---

## Table of contents

- [What this platform does](#what-this-platform-does)
- [System at a glance](#system-at-a-glance)
- [The components and how they relate](#the-components-and-how-they-relate)
- [How a query flows through the system](#how-a-query-flows-through-the-system)
- [How the simulation actually works](#how-the-simulation-actually-works)
- [Why the same design serves multiple domains](#why-the-same-design-serves-multiple-domains)
- [Key decisions and risks to watch](#key-decisions-and-risks-to-watch)
- [Repository layout](#repository-layout)
- [Quickstart (local dev)](#quickstart-local-dev)
- [Running without hardware (CI / dev laptops)](#running-without-hardware-ci--dev-laptops)
- [LLM backend configuration](#llm-backend-configuration)
- [OpenShift Deployment](#openshift-deployment)
- [Adding a domain](ADD_DOMAIN.md)

---

## What this platform does

This system answers impact and response questions about a live operational environment when a disruptive event is layered on top of it. In plain terms: it takes a real-time picture of what is happening, overlays a hypothetical or unfolding disruption, and reasons over the combination to answer questions like *“how does this event affect the current situation?”* and *“how should things be rerouted or rescheduled in response?”*

The design is deliberately **domain-agnostic**. The original framing is a supply-chain scenario — a port closure or volcanic ash disrupting flights — but the same machinery applies unchanged to a manufacturing plant, where the disruption is a machine breakdown or material shortage. The core abstraction is the same in both: **live data + a dependency graph + a simulation-event overlay + staged reasoning.**

The whole platform is built to run on **OpenShift**, which is a hard constraint that shapes every technology choice below.

> **The one-sentence model:** A simulation event is an overlay; the LLM agent investigates it by calling graph-traversal, solver, vector-search, and ingestion tools in whatever order the question demands — then explains the result grounded in the numbers those tools returned.

---

## System at a glance

The platform is a small number of cooperating layers running inside one OpenShift cluster. The diagram below shows how they stack: an API and an orchestrator at the top, the three reasoning stages beneath, the LLM client as the inference/vector backend, and separate Neo4j and Postgres instances for graph and geo/vector state. Two things are worth noticing immediately — the reasoning stages are colour-coded by whether they use the LLM, and both the graph (Neo4j) and geo (PostGIS) paths bypass the LLM client to be queried directly.

![Layered system overview](docs/images/architecture-overview.png)

*Figure 1 — Layered system overview. Everything runs inside OpenShift. The LLM client fronts inference and vector/RAG; graph (Neo4j) and live/geo (PostGIS) are queried directly.*

The remaining sections walk through each component: what it is, why it is there, and how it relates to its neighbours.

---

## The components and how they relate

### OpenShift — the platform

OpenShift is the deployment substrate and a fixed requirement, not an interchangeable choice. Every other component is selected partly because it runs cleanly on OpenShift: Postgres via an operator, vLLM via OpenShift AI / KServe, and the application services as ordinary Deployments and CronJobs. Treating OpenShift as the constant is what lets the rest of the stack stay portable across domains.

### vLLM — local inference (optional)

vLLM serves an open-weight language model locally on GPU, keeping inference inside your cluster. It exposes an OpenAI-compatible `/v1` interface. To use it, set `LLM_BASE_URL=http://vllm.general-sim.svc:8080/v1`. The model must support **structured tool calling** if you use the tool-calling path.

### LLM client — the inference and RAG backend

The app talks to any OpenAI-compatible inference endpoint through `src/llm/openai_client.py`. Switching providers is a configuration change, not a code change:

| Provider | `LLM_BASE_URL` | `LLM_BACKEND` |
|---|---|---|
| OpenAI (default) | `https://api.openai.com/v1` | `openai` |
| vLLM (self-hosted) | `http://vllm.svc:8080/v1` | `openai` |
| Llama Stack `/v1` | `http://llamastack.svc:8321/v1` | `openai` |
| Llama Stack SDK | `http://llamastack.svc:8321` | `llamastack` |
| Tests / no GPU | *(any)* | `fake` |

Vector/RAG operations (embed, ingest, search) go directly to **pgvector** via asyncpg — no intermediate server required. A single `llm_embeddings` table in Postgres stores all collections.

> **Design rule:** Application code goes through `LLMClientBase` (`src/llm/`) for anything involving the model, embeddings, or vector search — never calling any inference API or pgvector SQL directly. The sole exceptions are graph (Neo4j) and live/geo (PostGIS), which are queried directly.

### Neo4j — the property graph

Neo4j holds the dependency graph and simulation-event overlays. It is queried directly via the official async Python driver using native Cypher. Entities and their dependency edges live here; `SimulationEvent` nodes are injected as overlays and can be removed without touching any other data. Neo4j runs as a StatefulSet in the cluster, deployed via the official `neo4j/neo4j` Helm chart.

### PostgreSQL — two jobs, two extensions

Postgres carries the remaining two persistence concerns. The dependency graph has moved to Neo4j, so the custom Postgres image only needs pgvector + PostGIS — no AGE extension required.

| Extension | Role | Accessed via |
|---|---|---|
| **pgvector** | Embeddings and RAG. Stores simulation-event narratives, playbooks, and precedent for retrieval. | Directly via asyncpg |
| **PostGIS** | The live “current situation” snapshot — entity positions, states, geospatial data — written by ingestion. | Directly via asyncpg |

### Ingestion — getting live data in

Ingestion adapters live under `domain/<name>/adapters/`. Each pulls from one
external source and normalises into a single **canonical schema** (id, type,
optional geometry, timestamp, status, and a free-form attributes field). The
shared runner in `src/ingestion/` upserts into PostGIS only — ground truth,
never the simulation overlay. Which domain packages load is controlled by
`ENABLED_DOMAINS`; which adapter a CronJob runs is `--adapter` / Helm
`adapterId`. Details: [ADD_DOMAIN.md](ADD_DOMAIN.md).

Each adapter runs two ways: as a scheduled OpenShift CronJob for steady polling, and as an on-demand callable that the reasoning agent can trigger mid-query when it needs current data.


### Admin UI — browse and manage data

A built-in single-page application is served at `GET /admin/`. It provides a read/write view over both stores without any extra tooling:

| Route | Description |
|---|---|
| `GET /admin/` | Admin SPA (HTML) |
| `GET /admin/stats` | Aggregate counts from Postgres and Neo4j |
| `GET /admin/entity-types` | Distinct entity types in the live store |
| `GET /admin/entities` | Paginated entity list with search/filter |
| `GET /admin/entities/{id}` | Entity detail and state history |
| `GET /admin/graph/nodes` | Entity nodes from Neo4j |
| `GET /admin/graph/scenarios` | Distinct scenario IDs |
| `GET /admin/graph/events` | SimulationEvent nodes (optional scenario filter) |
| `GET /admin/graph/edges` | All dependency / AFFECTED_BY edges |
| `POST /admin/graph/events` | Inject a new simulation event overlay |
| `DELETE /admin/graph/scenarios/{id}` | Remove a scenario from the graph and vector store |

### The ReAct agent pipeline

The pipeline (`src/reasoning/pipeline.py`) is a **ReAct (Reason + Act) agent loop**: the LLM is the top-level orchestrator. It decides which tools to call, in what order, and when it has gathered enough information to answer. A safety cap of six rounds prevents unbounded loops.

The LLM has four tools:

| Tool | Module | What it does |
|---|---|---|
| `get_affected_subgraph` | `src/graph/tool.py` | Neo4j Cypher traversal — finds every entity reachable from the simulation event via dependency edges, plus entity attributes (callsign, route, etc.) |
| `solve_impact` | `src/reasoning/pipeline.py` | Runs the Stage-2 solver on the affected subgraph — returns impact score, chain length, and ranked response options |
| `search_scenario_context` | `src/reasoning/search_tool.py` | pgvector semantic search over the scenario's event-narrative collection |
| `run_ingestion_pull` | `src/ingestion/tool.py` | On-demand live data refresh from a registered adapter |

The agent typically calls tools in the order above, but nothing enforces that sequence: a question about current positions may start with `run_ingestion_pull`; a simple clarification may skip the solver entirely. The `tool_call_trace` field in every `POST /query` response exposes each call the agent made and what it returned, making the reasoning fully auditable.

**The three underlying data-access functions** (Neo4j traversal, PostGIS + solver, pgvector search) are kept as independent modules (`stage1.py`, `stage2.py`, `stage3.py`). Each is independently testable and can be called directly — they are the tools' implementation, not the orchestration logic.

---

## How a query flows through the system

A `POST /query` request kicks off the ReAct agent loop. The diagram below captures the most common investigation path; the actual sequence depends on what the LLM decides to call.

![The query lifecycle](docs/images/query-flow.png)

*Figure 2 — The ReAct query lifecycle. The LLM decides which tools to call; every tool invocation and its result appear in the `tool_call_trace` field of the response.*

**Typical agent sequence for an impact question:**

```
POST /query  { question, scenario_id }
        │
        │  Round 1 — LLM calls get_affected_subgraph(scenario_id)
        ├────── Neo4j traversal → entity IDs + dependency edges + attributes
        │
        │  Round 2 — LLM calls solve_impact(scenario_id)
        ├────── PostGIS live state read + StubSolver → impact score + response options
        │
        │  Round 3 (optional) — LLM calls search_scenario_context(query, scenario_id)
        ├────── pgvector search → event narrative chunks
        │
        └────── LLM produces final answer grounded in tool outputs
                ↓
        QueryResponse { answer, affected_entities, solver, tool_call_trace }
```

The `tool_call_trace` in the response is the audit trail — it shows what the agent investigated and what each data source returned before the LLM wrote its answer.

---

## How the simulation actually works

The most important architectural decision is that a simulation **never mutates live data**. A simulation event is an overlay applied at query time: it is a node injected into the graph, connected by `AFFECTED_BY` edges to the entities it perturbs, with its narrative embedded separately in the vector store. The live snapshot is read **through the lens of** that event, but is left untouched.

This is what makes multiple concurrent what-if scenarios trivial — each is an independent overlay tagged by its own scenario id — and what makes them fully reversible: removing the event node resets everything in a single operation.

![The overlay mechanism](docs/images/simulation-overlay.png)

*Figure 3 — The overlay mechanism. Ground truth (left) is read-only at query time. The event and its affected-entity references (right) are injected and removable, leaving the base graph intact.*

> **What kind of simulation this is (and isn’t):** This is a **dependency-and-impact reasoning** engine: it propagates effects through a known graph and applies solver logic on top. It is **not** a tick-by-tick discrete-event physics simulation (e.g. AnyLogic). That is an intentional trade: you gain explainability, speed, and concurrent what-if scenarios; you give up stochastic second-by-second temporal dynamics. Because the Stage 2 solver is pluggable, a full discrete-event engine can be dropped into that slot later without changing anything else.

---

## Why the same design serves multiple domains

The platform is best understood as a domain-agnostic skeleton with well-defined
swap points. The skeleton — OpenShift, vLLM (optional), Postgres, Neo4j, the
ReAct pipeline, and the overlay mechanism — stays identical. Domain-specific
code lives under top-level **`domain/<name>/`** packages (adapters, optional
solvers). Which packages load is controlled by **`ENABLED_DOMAINS`**
(see [ADD_DOMAIN.md](ADD_DOMAIN.md)).

![Fixed core vs. swap seams](docs/images/domain-seams.png)

*Figure 4 — The fixed core (left) versus the per-domain swap seams (right). Domain adaptation touches only the right-hand column.*

The reason this works is that impact propagation is graph traversal in every domain. A port closure cascading through dependent routes and a stopped machine cascading through dependent cells are the **same** Cypher traversal over a **different** schema. The table below makes the mapping concrete.

| Layer | Supply chain | Manufacturing plant |
|---|---|---|
| Ingestion | Flight / AIS / freight APIs (`domain/…/adapters`) | OPC-UA, MQTT, SCADA, historian |
| Graph schema | Port, Route, Region | ISA-95: Site → Area → Work Cell → Equipment |
| Simulation event | Port closure, volcanic ash | Machine breakdown, material shortage |
| Solver (Stage 2) | Route pathfinding (`domain/…/solver.py`) | Production rescheduling / line balancing |
| RAG context | Logistics precedent | SOPs, maintenance manuals, playbooks |

A manufacturing note worth flagging: plant sensor data is far higher-frequency than logistics data, so that domain leans harder on the historian/time-series side and may add a time-series extension or a downsampling step in ingestion. That is an ingestion-layer concern — it does not disturb the core.

Shipped today:

| Domain id | Package | Adapters |
|---|---|---|
| `aviation` (default) | `domain/aviation/` | `opensky_flights` |
| `earthquakes` | `domain/earthquakes/` | `usgs_earthquakes` |

```bash
ENABLED_DOMAINS=aviation          # default
ENABLED_DOMAINS=aviation,earthquakes
uv run ingest-run --adapter opensky_flights
```

---

## Key decisions and risks to watch

1. **Two separate graph stores** (Neo4j for the dependency graph, Postgres for live data and embeddings) keep concerns cleanly separated; validate Neo4j connectivity and the Helm chart deployment early.
2. **The vLLM model must support structured tool calling**, with tool-calling enabled at serve time (`--enable-auto-tool-choice`) — the ReAct pipeline depends on the model correctly emitting tool calls and text completions.
3. **The agent loop owns orchestration; the data-access modules stay pure.** `stage1.py`, `stage2.py`, and `stage3.py` contain no agent logic — they are called by the pipeline dispatcher and remain independently testable.
4. **Graph stays in Neo4j, geo stays in Postgres.** The LLM client owns inference, embeddings, and vector search only — it is not a front door for all state.
5. **Live data and simulation knowledge stay separate.** The overlay must never mutate ground truth; this is what enables concurrent, reversible what-if scenarios.
6. **The `tool_call_trace` is the reasoning audit trail.** Every `QueryResponse` includes the ordered list of tool calls the agent made — use this to debug or explain any answer.

> In short: a fixed OpenShift-native skeleton handles platform, inference, storage, and agentic reasoning identically across domains, while domain packages under `domain/` — adapters, optional solvers, and related wiring — are all that change to retarget it from supply chains to manufacturing plants. See [ADD_DOMAIN.md](ADD_DOMAIN.md).

---

## Repository layout

```
domain/                      # Domain packages (adapters, optional solvers)
  aviation/
    adapters/                # e.g. opensky_flights
  earthquakes/
    adapters/                # e.g. usgs_earthquakes
src/
  core/                      # Domain-agnostic abstractions, interfaces, and Settings
  ingestion/
    registry.py              # ENABLED_DOMAINS catalog + adapter/solver resolution
    runner.py                # Canonical ingest loop
    tool.py                  # run_ingestion_pull tool schema + callable
  graph/
    bootstrap.py             # Idempotent DDL for Postgres + Neo4j
    nodes.py                 # Entity CRUD + dependency edge helpers
    events.py                # SimulationEvent overlay inject / remove
    cypher.py                # neo4j_session() context manager
    tool.py                  # get_affected_subgraph tool schema + callable
  reasoning/
    pipeline.py              # ReAct agent loop — top-level orchestrator
    stage1.py                # Neo4j graph traversal (called by pipeline dispatcher)
    stage2.py                # PostGIS live state read + solver (called by pipeline dispatcher)
    stage3.py                # Standalone synthesis helper (vector search + single generate())
    search_tool.py           # search_scenario_context tool schema + callable
    types.py                 # QueryRequest / QueryResponse / ToolCallRecord
  solver/
    stub.py                  # StubSolver (fallback; domain solvers live under domain/)
    tool.py                  # solve_impact tool schema + legacy callable
  llm/
    base.py                  # LLMClientBase protocol
    openai_client.py         # OpenAI-compatible inference + pgvector RAG
    fake.py                  # FakeLLMClient for tests (supports response_sequence)
    types.py                 # Message / ToolCall / GenerateResult / Chunk
  api/                       # FastAPI entrypoint + admin SPA
deploy/                      # Containerfiles, Helm charts, OpenShift manifests
tests/
```

---

## Quickstart (local dev)

### 1. Install dependencies

```bash
uv sync --all-extras
```

### 2. Start local services (Postgres + Neo4j)

```bash
docker compose up -d
```

This starts Postgres (pgvector + PostGIS) on port 5432 and Neo4j on ports 7474
(Browser UI) and 7687 (Bolt).  Wait for both healthchecks to pass, then run the
schema bootstrap:

```bash
uv run python -m src.graph.bootstrap
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env: set POSTGRES_DSN, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
# LLM_* settings, and optionally ENABLED_DOMAINS (default: aviation).
#
# With compose defaults:
#   POSTGRES_DSN=postgresql://sim:sim@localhost:5432/sim
#   NEO4J_URI=bolt://localhost:7687
#   NEO4J_USER=neo4j
#   NEO4J_PASSWORD=sim
#   ENABLED_DOMAINS=aviation
```

### 4. Run the API

```bash
uv run python -m src.api.main
# or:
uv run uvicorn src.api.app:app --reload
```

Visit `http://localhost:8000/health` — returns `{"status": "ok", "db": "reachable"}` when Postgres is reachable.
Visit `http://localhost:8000/admin/` for the admin SPA (requires both Postgres and Neo4j).

### 5. Run tests (no GPU or live Llama Stack required)

```bash
uv run pytest
```


### 6. Demo against a live deployment

Two helpers are included for smoke-testing a running cluster:

```bash
# Run a canned query against the deployed API
./demo.sh [scenario_id] [question]

# Seed the graph and Postgres with synthetic demo data
uv run python scripts/seed_demo.py
```

`demo.sh` defaults to the supply-chain scenario (Port of Los Angeles closure).
`seed_demo.py` creates sample entity types, dependency edges, and a simulation event so the full pipeline can be exercised end to end.

---

## Running without hardware (CI / dev laptops)

Set `LLM_BACKEND=fake` in `.env` (or the environment).  This swaps in
`FakeLLMClient` which returns canned completions, embeddings, and vector
search hits, so the full reasoning pipeline can be exercised in tests without a
GPU or a running Llama Stack server.

---

## LLM backend configuration

The app uses any OpenAI-compatible inference endpoint. The backend is selected
by the `LLM_BACKEND` environment variable.

### Pointing at a self-hosted vLLM for local dev

1. Start vLLM locally with tool-calling enabled:

```bash
vllm serve meta-llama/Llama-3.1-8B-Instruct \
    --enable-auto-tool-choice \
    --tool-call-parser hermes
```

2. Set in `.env`:

```bash
LLM_BASE_URL=http://localhost:8080/v1
OPENAI_API_KEY=unused
LLM_BACKEND=openai
GENERATION_MODEL_ID=meta-llama/Llama-3.1-8B-Instruct
EMBEDDING_MODEL_ID=all-MiniLM-L6-v2
EMBEDDING_DIMENSION=384
```

### Pointing at Llama Stack (future)

Llama Stack exposes an OpenAI-compatible `/v1` endpoint. No code change needed:

```bash
LLM_BASE_URL=http://llamastack:8321/v1
OPENAI_API_KEY=unused
LLM_BACKEND=openai
```

Archived Llama Stack Helm chart and build configs are preserved under
`deploy/archived/` if you want to bring it back as a sidecar.

### Running without a GPU (CI / dev laptops)

Set `LLM_BACKEND=fake` in `.env`. `FakeLLMClient` provides:
- Deterministic embeddings (hash-seeded unit vectors, correct dimension)
- In-memory vector store (ingest then search, cosine similarity)
- `canned_tool_calls` — emitted once then cleared, for single-round tool tests
- `response_sequence` — an ordered queue of `GenerateResult` objects popped on each `generate()` call; use this to simulate a full multi-step ReAct trace in tests without a real model

```bash
LLM_BACKEND=fake
```


## OpenShift Deployment

Deployment is driven by a **Makefile** that wraps `podman build/push` for
images and **Helm** for all Kubernetes resources.  Each component has its own
Helm chart under `deploy/helm/` so components can be upgraded independently.

### Prerequisites

| Requirement | Notes |
|---|---|
| OpenShift 4.13+ | Tested against OCP 4.14/4.15 |
| `oc` CLI logged in | `oc login ...` — needs cluster-admin (or a role covering Deployments, StatefulSets, Services, Routes, Jobs, CronJobs, Secrets, ConfigMaps, ServiceAccounts, and ClusterRoleBindings) |
| `helm` 3.x | [Install Helm](https://helm.sh/docs/intro/install/) |
| `podman` | To build and push images |
| GPU nodes | Required for vLLM only; CPU nodes are sufficient for everything else |
| NVIDIA GPU Operator | Install via OperatorHub if using the plain vLLM Deployment |
| (Optional) Red Hat OpenShift AI | Only needed for the KServe `InferenceService` vLLM path |

No additional operators are required. Postgres runs as a plain StatefulSet.

---

### Quick start — full deploy

```bash
# 1. Log in to quay.io so podman can push images
podman login quay.io

# 2. Build and push all three container images
make build

# 3. Deploy every component in dependency order
#    PG_PASSWORD is injected via --set; never stored in values files.
make deploy PG_PASSWORD=<your-password>
```

`make deploy` runs the steps below in order, waiting for each to be healthy before proceeding. It deploys Postgres, Neo4j, the schema bootstrap Job, vLLM (optional), the API, and the ingestion CronJob.

---

### Helm chart overview

| Chart | Path | Key resources |
|---|---|---|
| `postgres` | `deploy/helm/postgres` | StatefulSet, 2 Services, ServiceAccount, ClusterRoleBinding (anyuid SCC), Secret, ConfigMap (init SQL) |
| `neo4j` | `neo4j/neo4j` (official chart) | StatefulSet, Services, OpenShift Route (Browser UI) |
| `bootstrap` | `deploy/helm/bootstrap` | Job (Helm post-install/upgrade hook — auto-deleted on success) |
| `vllm` | `deploy/helm/vllm` | Deployment, Service, PVC (30 Gi) |
| `llamastack` | `deploy/archived/llamastack-helm` | (archived — see `deploy/archived/` to restore) |
| `api` | `deploy/helm/api` | Deployment (2 replicas), Service, OpenShift Route, ConfigMap, Secret |
| `ingestion` | `deploy/helm/ingestion` | CronJob (every 10 min, `concurrencyPolicy: Forbid`) |

---

### Step 1 — Build and push container images

```bash
# Build all images (postgres + app) and push to quay.io/robertsandoval/
make build

# Or build individual images:
make build-postgres
make build-app
```

Override the registry or tag if needed:

```bash
make build REGISTRY=quay.io/myorg TAG=v1.2.3
```

---

### Step 2 — Deploy Postgres

```bash
make deploy-postgres PG_PASSWORD=<your-password>
```

This installs the `postgres` Helm chart which:
- Creates the `general-sim` namespace (idempotent)
- Applies a `ClusterRoleBinding` granting `anyuid` SCC to the `postgres-sa` ServiceAccount (so the container can run as UID 999)
- Creates the `postgres-credentials` Secret from `--set postgres.password=...`
- Mounts an init-SQL ConfigMap that enables the `vector` and `postgis` extensions on first startup
- Deploys a StatefulSet with a 10 Gi PVC and readiness/liveness probes

Wait for Postgres to be ready:

```bash
oc rollout status statefulset/postgres -n general-sim --timeout=300s
```

---

### Step 3 — Run the schema bootstrap Job

```bash
make deploy-bootstrap PG_PASSWORD=<your-password>
```

The `bootstrap` chart deploys a Job as a Helm `post-install,post-upgrade` hook.
Helm waits for the Job to complete before marking the release successful
(`--atomic --timeout 3m`).  The Job is deleted automatically on success.
Re-running `make deploy-bootstrap` is fully idempotent.

---

### Step 4 — Deploy vLLM

```bash
make deploy-vllm
```

Deploys the `vllm` chart (plain Deployment + 30 Gi PVC).  The Deployment
targets GPU nodes via `nodeSelector: nvidia.com/gpu.present: "true"` and
runs vLLM with `--enable-auto-tool-choice` and `--tool-call-parser=llama3_json`
so Llama Stack tool calling works correctly.

> The `--wait --timeout 15m` flag is used here because the GPU pod may take
> several minutes to pull the model weights on first start.

**Alternative — KServe InferenceService** (requires OpenShift AI / RHOAI):

```bash
oc apply -f deploy/openshift/vllm/inferenceservice.yaml
```

---

### Step 5 — Deploy the API and ingestion CronJob

```bash
make deploy-api        PG_PASSWORD=<your-password> OPENAI_API_KEY=<your-key>
make deploy-ingestion  PG_PASSWORD=<your-password> OPENAI_API_KEY=<your-key>
```

The `api` chart creates 2 replicas with topology spread across nodes and an
OpenShift Route with TLS edge termination.

Smoke test after deploy:

```bash
ROUTE=$(oc get route general-sim-api -n general-sim -o jsonpath='{.spec.host}')
curl -s https://$ROUTE/health | jq .
# Expected: {"status": "ok", "db": "reachable"}
```

Trigger the ingestion job immediately to verify end-to-end:

```bash
oc create job ingestion-manual \
  --from=cronjob/general-sim-ingestion \
  -n general-sim

oc wait job/ingestion-manual \
  -n general-sim --for=condition=complete --timeout=120s
```

---

### Per-component upgrades

After changing code or config, rebuild the affected image and upgrade only that
chart — no need to re-deploy everything:

```bash
make build-app
make deploy-api PG_PASSWORD=<your-password>
```

To upgrade a chart's non-secret values, edit `deploy/helm/<chart>/values.yaml`
and re-run the `make deploy-<chart>` target.  Secrets are always supplied via
`--set` and are never stored in values files.

---

### Tear-down

```bash
make undeploy
# PVCs are NOT deleted automatically — remove manually if needed:
# oc delete pvc -n general-sim --all
```

---

### Makefile reference

```bash
make help                                            # List all targets and variables
make build                                           # Build and push all images
make deploy PG_PASSWORD=<pw> NEO4J_PASSWORD=<pw>    # Full ordered deploy
make deploy-neo4j NEO4J_PASSWORD=<pw>               # Deploy only Neo4j
make neo4j-connect                                  # Port-forward Neo4j locally
make status                                         # helm list + oc get pods
make lint-charts                                    # helm lint all charts
make undeploy                                       # Uninstall all releases
```

Override defaults on the command line:

| Variable | Default | Description |
|---|---|---|
| `REGISTRY` | `quay.io/robertsandoval` | Image registry root |
| `NAMESPACE` | `general-sim` | Target OpenShift namespace |
| `TAG` | `latest` | Image tag for all built images |
| `PG_PASSWORD` | *(none)* | Postgres password — required for deploy targets |
| `NEO4J_PASSWORD` | *(none)* | Neo4j password — required for deploy and bootstrap targets |
| `OPENAI_API_KEY` | *(none)* | API key for the inference endpoint |

---

### In-cluster service FQDNs

| Service | URL |
|---|---|
| Postgres | `postgres.general-sim.svc:5432` |
| Neo4j Bolt | `bolt://neo4j.general-sim.svc:7687` |
| Neo4j HTTP | `http://neo4j.general-sim.svc:7474` |
| vLLM | `http://vllm.general-sim.svc:8080` |
| API | `http://general-sim-api.general-sim.svc:8000` |

---

Raw Kubernetes manifests (pre-Helm) are preserved under `deploy/openshift/` for
reference.  The Helm charts under `deploy/helm/` are the authoritative
deployment path going forward.
