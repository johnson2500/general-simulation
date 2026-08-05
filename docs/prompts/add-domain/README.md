# Cursor prompts: add a domain

Paste these into Cursor **one at a time** (Prompt 0 → 1 → …). Fill in the
`<placeholders>` before sending. Keep [ADD_DOMAIN.md](../../../ADD_DOMAIN.md)
open as the human checklist — these prompts drive an agent through the same
steps without rewriting core platform code.

**Hard rules for every prompt**

- New code lives under `domain/<DOMAIN_ID>/`
- Register adapters only via `DOMAIN_CATALOG` in `src/ingestion/registry.py`
- Never put domain names in `src/core`, `src/reasoning`, `src/graph` helpers, or `src/ingestion/runner.py`
- Domain fields go in `CanonicalEntity.attributes` only
- Tests must be offline (fixture + `normalize`, no live network/DB)

Reference implementations: `domain/earthquakes/adapters/usgs_earthquakes.py`,
`tests/fixtures/usgs_earthquakes.json`, `tests/test_ingestion.py`.

---

## Prompt 0 — Orient (run once)

```text
You are helping me add a new domain to this simulation platform.

Read and summarize (do not edit yet):
- ADD_DOMAIN.md
- README.md sections on ingestion and "Why the same design serves multiple domains"
- domain/aviation/ and domain/earthquakes/ as reference packages
- src/ingestion/registry.py
- src/core/ingestion.py
- src/core/config.py (enabled_domains)
- tests/test_registry.py and tests/test_ingestion.py

Then answer:
1. Exact file checklist for a new domain named <DOMAIN_ID>
2. What must NOT be changed in src/core, src/reasoning, src/graph, src/ingestion/runner.py
3. How ENABLED_DOMAINS and adapter_id interact

Wait for my domain description before writing code.
```

---

## Prompt 1 — Domain package + adapter

```text
Add a new domain package for: <DOMAIN_ID>

Context:
- <1–3 sentences: what real-world system this models>
- Live data source: <API / feed URL or protocol>
- Auth: <none | API key env var name>
- Entity identity scheme: prefix like "<source>-<id>"
- Suggested entity type string: moving_entity | fixed_node | <generic label>
- Status values: <list>
- Domain fields that belong in attributes: <list>
- Geometry: Point / Polygon / none

Requirements:
1. Create domain/<DOMAIN_ID>/__init__.py and domain/<DOMAIN_ID>/adapters/__init__.py
2. Create domain/<DOMAIN_ID>/adapters/<adapter_id>.py implementing IngestionAdapter
   (adapter_id class attr, async fetch, pure normalize → list[CanonicalEntity])
3. Mirror style of domain/earthquakes/adapters/usgs_earthquakes.py
4. Put ALL domain-specific fields in attributes; do not extend CanonicalEntity
5. Skip records that cannot become valid entities (e.g. missing id / coords if geometry required)
6. Do NOT register in DOMAIN_CATALOG yet — that is the next step
7. Do NOT edit src/core, src/reasoning, or src/ingestion/runner.py

When done, show the new file tree and a short note on the id/status/attributes mapping.
```

---

## Prompt 2 — Register + enable

```text
Register the domain I just created.

1. Add a DomainSpec to DOMAIN_CATALOG in src/ingestion/registry.py with:
   - domain_id: <DOMAIN_ID>
   - adapters: { "<adapter_id>": "domain.<DOMAIN_ID>.adapters.<module>:<ClassName>" }
2. Update .env.example comment listing known domain ids
3. Tell me the exact ENABLED_DOMAINS value to set locally
4. Do not change Helm/OpenShift yet

Verify by reading registry imports resolve (no circular imports) and summarizing
how list_adapter_ids / get_adapter_class will behave with
ENABLED_DOMAINS=<DOMAIN_ID>.
```

---

## Prompt 3 — Fixture + tests

```text
Add offline tests for adapter <adapter_id>.

1. Record a small realistic fixture at tests/fixtures/<adapter_id>.json
   (trim to a few records; include at least one row that normalize should skip)
2. Add tests (new file tests/test_<adapter_id>.py OR extend tests/test_ingestion.py
   if that matches repo style) covering:
   - normalize count / id prefix / type / status / attributes keys
   - bad rows skipped
   - IngestionAdapter protocol conformance if useful
3. Ensure tests need no network and no live DB
4. Update or add a registry assertion in tests/test_registry.py for the new domain

Run: uv run pytest tests/test_registry.py tests/test_<adapter_id>.py -v
(or the ingestion test file you extended). Fix failures.
```

---

## Prompt 4 — Dependency graph bootstrap (optional)

```text
Wire a minimal Neo4j dependency graph for domain <DOMAIN_ID>.

Read src/graph/nodes.py (create_entity_node, create_dependency_edge, EDGE_*).
Read scripts/seed_demo.py only as a pattern — do not aviation-hardcode in core.

Create domain/<DOMAIN_ID>/bootstrap_graph.py (or scripts/seed_<DOMAIN_ID>.py) that:
- Creates a few fixed infrastructure Entity nodes
- Creates DEPENDS_ON / FEEDS / CARRIES edges as appropriate
- Documents how ingested live entity ids should connect to those nodes
- Is idempotent where practical

Do not mutate live PostGIS from this script unless seeding demo geometries
is explicitly required. Explain how Stage-1 traversal will use these edges.
```

---

## Prompt 5 — Domain solver (optional)

```text
Add an optional Stage-2 solver for <DOMAIN_ID>.

Read src/core/solver.py (Solver protocol, AffectedSubgraph, LiveState, SolverResult)
and src/solver/stub.py. Neither aviation nor earthquakes ships a solver today —
StubSolver is the fallback.

1. Create domain/<DOMAIN_ID>/solver.py implementing Solver
2. Domain maths only; no Neo4j/Postgres I/O inside solve()
3. Set DomainSpec.solver = "domain.<DOMAIN_ID>.solver:<ClassName>" in registry.py
4. Note resolve_solver() behavior: exactly one enabled domain solver → use it;
   else StubSolver

Add a focused unit test with a fake AffectedSubgraph + LiveState.
```

---

## Prompt 6 — Deploy / ingest wiring

```text
Wire runtime config so this domain can run in local + Helm paths.

1. Document .env:
   ENABLED_DOMAINS=<DOMAIN_ID>
   # or comma-join with aviation if needed
2. Show CLI: uv run ingest-run --adapter <adapter_id>
3. Update deploy/helm/ingestion/values.yaml (and api if needed) examples OR
   document the --set overrides:
   enabledDomains=<DOMAIN_ID>
   adapterId=<adapter_id>
4. Mention deploy/openshift/shared/configmaps.yaml ENABLED_DOMAINS if relevant
5. Do not invent new CronJob templates unless multiple adapters need different schedules

End with a short "smoke test" checklist for a human operator.
```

---

## Master prompt (single-shot alternative)

Use this only if you prefer one paste instead of Prompts 0–6:

```text
Implement a new domain for this repo following ADD_DOMAIN.md and the existing
aviation/earthquakes packages under domain/.

Domain brief:
- domain_id: <DOMAIN_ID>
- adapter_id: <adapter_id>
- source: <URL/docs>
- entity mapping: <id / type / status / geometry / attributes>
- enable locally via ENABLED_DOMAINS
- include fixture + pytest
- optional: graph bootstrap script, solver, Helm value notes

Hard rules:
- New code lives under domain/<DOMAIN_ID>/
- Register only via DOMAIN_CATALOG in src/ingestion/registry.py
- Never put domain names in src/core, src/reasoning, src/graph helpers, or runner.py
- Domain fields go in CanonicalEntity.attributes only
- Tests must be offline

Use domain/earthquakes/adapters/usgs_earthquakes.py and tests/test_ingestion.py
as the primary implementation/test patterns. After changes, run the new tests
and report the checklist from ADD_DOMAIN.md with done/skipped items.
```
