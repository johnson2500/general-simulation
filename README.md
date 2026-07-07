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

---

## What this platform does

This system answers impact and response questions about a live operational environment when a disruptive event is layered on top of it. In plain terms: it takes a real-time picture of what is happening, overlays a hypothetical or unfolding disruption, and reasons over the combination to answer questions like *“how does this event affect the current situation?”* and *“how should things be rerouted or rescheduled in response?”*

The design is deliberately **domain-agnostic**. The original framing is a supply-chain scenario — a port closure or volcanic ash disrupting flights — but the same machinery applies unchanged to a manufacturing plant, where the disruption is a machine breakdown or material shortage. The core abstraction is the same in both: **live data + a dependency graph + a simulation-event overlay + staged reasoning.**

The whole platform is built to run on **OpenShift**, which is a hard constraint that shapes every technology choice below.

> **The one-sentence model:** A simulation event is an overlay that triggers a graph traversal to find what is affected, a solver to quantify it and compute responses, and an LLM to explain it — all read against a live snapshot that is never mutated.

---

## System at a glance

The platform is a small number of cooperating layers running inside one OpenShift cluster. The diagram below shows how they stack: an API and an orchestrator at the top, the three reasoning stages beneath, the LLM client as the inference/vector backend, and a single Postgres instance holding all state. Two things are worth noticing immediately — the reasoning stages are colour-coded by whether they use the LLM, and the graph/geo path bypasses the LLM client to talk to Postgres directly.

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

Ingestion adapters pull from external sources and normalise whatever they return into a single **canonical schema** (id, type, optional geometry, timestamp, status, and a free-form attributes field). Each adapter knows one source; the normalisation step is what keeps the rest of the system source-agnostic. Adapters write **only** into the PostGIS live snapshot — they establish ground truth and never touch the simulation overlay.

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

### The reasoning orchestrator and its three stages

A thin orchestrator (LangGraph or a plain state machine) owns the top-level flow. It deliberately keeps control rather than handing the whole query to Llama Stack’s agent loop, because two of the three stages are intentionally non-LLM and a generative agent loop would fight that structure. The three stages separate cleanly by responsibility:

- **Stage 1 — Structural (deterministic, no LLM):** a Cypher traversal over Neo4j that walks dependency edges from the event to find every structurally affected entity. Answers *what is affected.*
- **Stage 2 — Quantitative (solver, no LLM):** a pluggable solver (OR-Tools initially) that reads live state through the lens of the event and computes magnitude and ranked response options. Answers *how much, and what to do.*
- **Stage 3 — Synthesis (LLM):** the model takes the affected set, the solver’s numbers, and vector-retrieved context and produces a grounded, cited explanation. It *explains*; it never invents the impact numbers.

Keeping these three independently swappable is the heart of the design: the structural, quantitative, and explanatory concerns never blur into one another.

---

## How a query flows through the system

The diagram below traces a single question end to end. Notice that the deterministic stages (green) run before the generative one (orange), and that the final response carries the structured evidence alongside the prose so the answer is auditable rather than a black box.

![The query lifecycle](docs/images/query-flow.png)

*Figure 2 — The query lifecycle. Deterministic graph traversal and solver run first; the LLM synthesises last, grounded in their output.*

---

## How the simulation actually works

The most important architectural decision is that a simulation **never mutates live data**. A simulation event is an overlay applied at query time: it is a node injected into the graph, connected by `AFFECTED_BY` edges to the entities it perturbs, with its narrative embedded separately in the vector store. The live snapshot is read **through the lens of** that event, but is left untouched.

This is what makes multiple concurrent what-if scenarios trivial — each is an independent overlay tagged by its own scenario id — and what makes them fully reversible: removing the event node resets everything in a single operation.

![The overlay mechanism](docs/images/simulation-overlay.png)

*Figure 3 — The overlay mechanism. Ground truth (left) is read-only at query time. The event and its affected-entity references (right) are injected and removable, leaving the base graph intact.*

> **What kind of simulation this is (and isn’t):** This is a **dependency-and-impact reasoning** engine: it propagates effects through a known graph and applies solver logic on top. It is **not** a tick-by-tick discrete-event physics simulation (e.g. AnyLogic). That is an intentional trade: you gain explainability, speed, and concurrent what-if scenarios; you give up stochastic second-by-second temporal dynamics. Because the Stage 2 solver is pluggable, a full discrete-event engine can be dropped into that slot later without changing anything else.

---

## Why the same design serves multiple domains

The platform is best understood as a domain-agnostic skeleton with four well-defined swap points. The skeleton — OpenShift, vLLM (optional), Postgres, the three-stage pipeline, and the overlay mechanism — stays identical. Only four seams change when you move from supply chain to manufacturing.

![Fixed core vs. swap seams](docs/images/domain-seams.png)

*Figure 4 — The fixed core (left) versus the four per-domain swap seams (right). Domain adaptation touches only the right-hand column.*

The reason this works is that impact propagation is graph traversal in every domain. A port closure cascading through dependent routes and a stopped machine cascading through dependent cells are the **same** Cypher traversal over a **different** schema. The table below makes the mapping concrete.

| Layer | Supply chain | Manufacturing plant |
|---|---|---|
| Ingestion | Flight / AIS / freight APIs | OPC-UA, MQTT, SCADA, historian |
| Graph schema | Port, Route, Region | ISA-95: Site → Area → Work Cell → Equipment |
| Simulation event | Port closure, volcanic ash | Machine breakdown, material shortage |
| Solver (Stage 2) | Route pathfinding | Production rescheduling / line balancing |
| RAG context | Logistics precedent | SOPs, maintenance manuals, playbooks |

A manufacturing note worth flagging: plant sensor data is far higher-frequency than logistics data, so that domain leans harder on the historian/time-series side and may add a time-series extension or a downsampling step in ingestion. That is an ingestion-layer concern — it does not disturb the core.

---

## Key decisions and risks to watch

1. **Two separate graph stores** (Neo4j for the dependency graph, Postgres for live data and embeddings) keep concerns cleanly separated; validate Neo4j connectivity and the Helm chart deployment early.
2. **The vLLM model must support structured tool calling**, with tool-calling enabled at serve time — validate this before committing, since the reasoning layer depends on it.
3. **Keep the orchestrator separate from any generative agent loop.** The deterministic stages must not be forced into a generative agent loop.
4. **Graph stays in Neo4j, geo stays in Postgres.** The LLM client owns inference, embeddings, and vector search only — it is not a front door for all state.
5. **Live data and simulation knowledge stay separate.** The overlay must never mutate ground truth; this is what enables concurrent, reversible what-if scenarios.

> In short: a fixed OpenShift-native skeleton handles platform, inference, storage, and reasoning identically across domains, while four narrow seams — ingestion, graph schema, solver, and RAG context — are all that change to retarget it from supply chains to manufacturing plants.

---

## Repository layout

```
src/
  core/        # Domain-agnostic abstractions, interfaces, and Settings
  ingestion/   # Ingestion agent framework + adapters (registered as Llama Stack tools)
  graph/       # Neo4j graph access (Cypher) — direct to Neo4j driver
  live/        # PostGIS live snapshot store — direct to Postgres
  reasoning/   # 3-stage pipeline: traversal → solver → synthesis
  solver/      # Pluggable quantitative solver interface + stub impl
  llm/         # LLM client: OpenAI-compatible inference + direct pgvector RAG
  api/         # FastAPI entrypoint
deploy/        # Containerfiles, OpenShift manifests, LlamaStackDistribution CR
tests/
```

---

## Quickstart (local dev)

### 1. Install dependencies

```bash
uv sync --all-extras
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env: set POSTGRES_DSN, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
# and LLM_* settings.
```

### 3. Run the API

```bash
uv run python -m src.api.main
# or:
uv run uvicorn src.api.app:app --reload
```

Visit `http://localhost:8000/health` — returns `{"status": "ok", "db": "reachable"}` when Postgres is reachable.
Visit `http://localhost:8000/admin/` for the admin SPA (requires both Postgres and Neo4j).

### 4. Run tests (no GPU or live Llama Stack required)

```bash
uv run pytest
```


### 5. Demo against a live deployment

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
- Canned generation responses (configurable tool-call shape for pipeline tests)

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
# Build all images (postgres + app + llamastack) and push to quay.io/robertsandoval/
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
- Mounts an init-SQL ConfigMap that enables the `age`, `vector`, and `postgis` extensions on first startup
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
