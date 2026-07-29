# Simulation Console

PatternFly 6 React UI for the General Simulation platform. Talks to the local FastAPI API (`/health`, `/admin/*`, `POST /query`) via the Vite dev proxy.

## Prerequisites

- Node.js 20+
- Local API stack running (Postgres, Neo4j, FastAPI) — see below

## Local three-terminal workflow

From the **repository root**:

```bash
# Terminal 1 — data stores
podman compose up -d
uv run python -m src.graph.bootstrap
uv run python scripts/seed_demo.py   # optional demo scenario opensky-uk-closure-001

# Terminal 2 — API
cp .env.example .env   # LLM_BACKEND=fake is fine for UI work without a model
uv run uvicorn src.api.app:app --reload --host 0.0.0.0 --port 8000

# Terminal 3 — UI
cd apps/simulation-console
npm install
npm run dev
```

Open **http://localhost:5173**.

Vite proxies `/health`, `/query`, and `/admin` to `http://localhost:8000`. FastAPI also allows CORS from `localhost:5173` / `127.0.0.1:5173`.

## Screens

| Route | Purpose |
|---|---|
| `/` | Overview — health + store/graph counts |
| `/entities` | Live Postgres entities (filter, search, detail drawer) |
| `/map` | Supply chain map — PostGIS positions + simulation highlight overlay |
| `/graph` | Cytoscape dependency graph; `?scenario=` and `?highlight=` |
| `/scenarios` | List / inject / delete simulation overlays |
| `/query` | Impact ReAct query + solver + tool-call trace |

After pulling map-related changes, **re-run** `uv run python scripts/seed_demo.py` from the repo root so Postgres gets demo lon/lat geometries. Without that, `/map` will be empty even if Neo4j already has the scenario.

## Build

```bash
npm run build
```

Production static assets land in `dist/`. Serving them from FastAPI or an OpenShift Route is a follow-up.
