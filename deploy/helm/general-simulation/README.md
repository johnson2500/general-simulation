# general-simulation Helm chart

Umbrella chart for the General Simulation & Impact-Reasoning Platform.

## Modes

| Mode | How | Namespace |
|------|-----|-----------|
| **Standalone** | `helm upgrade --install … -n <ns>` | Whatever `-n` you pass (e.g. `general-simulation`) |
| **Subchart** | Parent `Chart.yaml` dependency + values | Same as the parent release |

In-cluster defaults use **short Service names** (`postgres`, `neo4j`, `general-sim-api`). Cross-namespace clients should use FQDNs such as `general-sim-api.<namespace>.svc:8000`.

## Chart repository (GitHub Pages)

Once published:

```bash
helm repo add general-simulation https://robertsandoval.github.io/general-simulation
helm repo update
helm search repo general-simulation
```

Parent / subchart dependency:

```yaml
dependencies:
  - name: general-simulation
    version: 0.1.0
    repository: https://robertsandoval.github.io/general-simulation
    condition: general-simulation.enabled
```

## Standalone install

```bash
# Create Neo4j SA + anyuid SCC (UID 7474) and auth secret first
oc new-project general-simulation   # or --create-namespace below
oc apply -f deploy/openshift/neo4j/serviceaccount.yaml -n general-simulation
sed 's/__NAMESPACE__/general-simulation/g' deploy/openshift/neo4j/scc-binding.yaml | oc apply -f -
oc create secret generic neo4j-auth \
  -n general-simulation \
  --from-literal=NEO4J_AUTH="neo4j/<NEO4J_PASSWORD>"

helm upgrade --install general-simulation general-simulation/general-simulation \
  --namespace general-simulation --create-namespace \
  --set postgres.postgres.password=<PG_PASSWORD> \
  --set api.postgres.password=<PG_PASSWORD> \
  --set api.neo4j.password=<NEO4J_PASSWORD> \
  --set bootstrap.postgres.password=<PG_PASSWORD> \
  --set bootstrap.neo4j.password=<NEO4J_PASSWORD> \
  --set ingestion.postgres.password=<PG_PASSWORD> \
  --set ingestion.neo4j.password=<NEO4J_PASSWORD> \
  --set-string api.llm.apiKey=<OPENAI_API_KEY> \
  --wait --timeout 15m
```

From a local clone (before/without Pages):

```bash
make package-chart
helm upgrade --install general-simulation ./deploy/helm/general-simulation \
  --namespace general-simulation --create-namespace \
  # … same --set flags as above
```

## Client URL

| Client location | `GENERAL_SIMULATION_BASE_URL` |
|-----------------|-------------------------------|
| Same namespace (subchart) | `http://general-sim-api:8000` |
| Other namespace | `http://general-sim-api.<gen-sim-ns>.svc:8000` |

## Component toggles

| Key | Default | Notes |
|-----|---------|--------|
| `postgres.enabled` | `true` | Platform always brings its own Postgres |
| `neo4j.enabled` | `true` | Official `neo4j/neo4j` chart |
| `bootstrap.enabled` | `true` | Schema Job (hook) |
| `api.enabled` | `true` | FastAPI |
| `ingestion.enabled` | `true` | CronJob |
| `vllm.enabled` | `false` | Optional GPU inference |

## Publishing a new chart version

1. Bump `version` in `Chart.yaml`.
2. Tag `chart-v0.1.0` (or run the release workflow).
3. CI packages the chart and updates GitHub Pages (`index.yaml` + `.tgz`).
