# =============================================================================
# General Simulation & Impact-Reasoning Platform — Makefile
# =============================================================================
#
# Usage:
#   make build                                     Build and push all container images
#   make deploy PG_PASSWORD=<pw> NEO4J_PASSWORD=<pw>  Deploy all components in order
#   make undeploy                                  Uninstall all Helm releases
#   make status                                    Show Helm release + Pod status
#   make lint-charts                               Lint all Helm charts
#
# Individual component targets:
#   make build-postgres           Build and push the custom Postgres image
#   make build-app                Build and push the FastAPI app image
#   make deploy-postgres PG_PASSWORD=<pw>
#   make deploy-neo4j NEO4J_PASSWORD=<pw>
#   make deploy-bootstrap PG_PASSWORD=<pw> NEO4J_PASSWORD=<pw>
#   make deploy-vllm
#   make deploy-api PG_PASSWORD=<pw> NEO4J_PASSWORD=<pw>
#   make deploy-ingestion PG_PASSWORD=<pw>
#
# Variable overrides (pass on the command line):
#   REGISTRY         Image registry root  (default: quay.io/robertsandoval)
#   NAMESPACE        OpenShift namespace  (default: general-sim)
#   TAG              Image tag            (default: latest)
#   PG_PASSWORD      Postgres password    (no default — required for deploy targets)
#   NEO4J_PASSWORD   Neo4j password       (no default — required for deploy targets)
#
# Example:
#   make deploy PG_PASSWORD=s3cr3t NEO4J_PASSWORD=n3o4j! OPENAI_API_KEY=sk-...
#   make build TAG=v1.2.3
# =============================================================================

# ── Configurable variables ────────────────────────────────────────────────────
REGISTRY         ?= quay.io/robertsandoval
NAMESPACE        ?= general-sim
TAG              ?= latest
PG_PASSWORD      ?=
NEO4J_PASSWORD   ?=
OPENAI_API_KEY   ?=

# ── Derived image references ──────────────────────────────────────────────────
IMG_POSTGRES := $(REGISTRY)/general-sim-postgres:$(TAG)
IMG_APP      := $(REGISTRY)/general-sim-app:$(TAG)
IMG_VLLM     := docker.io/vllm/vllm-openai:v0.6.3

# ── Helm chart paths ──────────────────────────────────────────────────────────
CHART_POSTGRES  := deploy/helm/postgres
CHART_NEO4J     := deploy/helm/neo4j
CHART_BOOTSTRAP := deploy/helm/bootstrap
CHART_VLLM      := deploy/helm/vllm
CHART_API       := deploy/helm/api
CHART_INGESTION := deploy/helm/ingestion

# Common flags passed to every helm command
HELM_COMMON := --namespace $(NAMESPACE) --create-namespace

# ── Phony declarations ────────────────────────────────────────────────────────
.PHONY: all help \
        build build-postgres build-app \
        deploy deploy-postgres deploy-neo4j deploy-bootstrap deploy-vllm \
        deploy-api deploy-ingestion neo4j-connect \
        undeploy status lint-charts \
        _guard-pg-password _guard-neo4j-password _guard-oc _guard-helm _guard-podman

# ── Default target ────────────────────────────────────────────────────────────
all: help

# ── Help ──────────────────────────────────────────────────────────────────────
help:
	@printf "\nGeneral Simulation Platform — available targets:\n"
	@printf "  %-36s %s\n" "build"                               "Build and push all container images"
	@printf "  %-36s %s\n" "build-postgres"                      "Build and push Postgres image"
	@printf "  %-36s %s\n" "build-app"                           "Build and push FastAPI app image"
	@printf "  %-36s %s\n" "deploy PG_PASSWORD=<pw> NEO4J_PASSWORD=<pw>" "Deploy all components in order"
	@printf "  %-36s %s\n" "deploy-postgres"                     "Deploy only Postgres"
	@printf "  %-36s %s\n" "deploy-neo4j"                        "Deploy only Neo4j"
	@printf "  %-36s %s\n" "neo4j-connect"                       "Port-forward Neo4j and print connection URLs"
	@printf "  %-36s %s\n" "deploy-bootstrap"                    "Deploy only schema bootstrap Job"
	@printf "  %-36s %s\n" "deploy-vllm"                         "Deploy only vLLM (optional)"
	@printf "  %-36s %s\n" "deploy-api"                          "Deploy only FastAPI app"
	@printf "  %-36s %s\n" "deploy-ingestion"                    "Deploy only ingestion CronJob"
	@printf "  %-36s %s\n" "undeploy"                            "Uninstall all Helm releases"
	@printf "  %-36s %s\n" "status"                              "Show releases and pod status"
	@printf "  %-36s %s\n" "lint-charts"                         "Lint all Helm charts"
	@printf "\nVariables:\n"
	@printf "  %-18s %s\n" "REGISTRY"         "$(REGISTRY)"
	@printf "  %-18s %s\n" "NAMESPACE"        "$(NAMESPACE)"
	@printf "  %-18s %s\n" "TAG"              "$(TAG)"
	@printf "  %-18s %s\n" "PG_PASSWORD"      "(required for deploy targets — no default)"
	@printf "  %-18s %s\n" "NEO4J_PASSWORD"   "(required for deploy targets — no default)"
	@printf "\n"

# ── Guards ────────────────────────────────────────────────────────────────────
_guard-pg-password:
	@test -n "$(PG_PASSWORD)" || \
	  { printf "ERROR: PG_PASSWORD is required.\nRun: make <target> PG_PASSWORD=<password>\n"; exit 1; }

_guard-neo4j-password:
	@test -n "$(NEO4J_PASSWORD)" || \
	  { printf "ERROR: NEO4J_PASSWORD is required.\nRun: make <target> NEO4J_PASSWORD=<password>\n"; exit 1; }

_guard-oc:
	@command -v oc >/dev/null 2>&1 || \
	  { echo "ERROR: 'oc' CLI not found. Install the OpenShift CLI and run 'oc login'."; exit 1; }

_guard-helm:
	@command -v helm >/dev/null 2>&1 || \
	  { echo "ERROR: 'helm' CLI not found. Install Helm 3+ from https://helm.sh/docs/intro/install/"; exit 1; }

_guard-podman:
	@command -v podman >/dev/null 2>&1 || \
	  { echo "ERROR: 'podman' not found. Install Podman or substitute 'docker' by setting PODMAN=docker."; exit 1; }

# ── Container image builds ────────────────────────────────────────────────────
build: _guard-podman build-postgres build-app
	@echo "==> All images built and pushed to $(REGISTRY)."

build-postgres: _guard-podman
	@echo "==> Building Postgres image: $(IMG_POSTGRES)"
	podman build \
	  --platform=linux/amd64 \
	  -f deploy/postgres/Containerfile \
	  -t $(IMG_POSTGRES) \
	  deploy/postgres
	podman push $(IMG_POSTGRES)

build-app: _guard-podman
	@echo "==> Building FastAPI app image: $(IMG_APP)"
	podman build \
	  --platform=linux/amd64 \
	  -f deploy/app/Containerfile \
	  -t $(IMG_APP) \
	  .
	podman push $(IMG_APP)

# ── Namespace bootstrap ───────────────────────────────────────────────────────
_deploy-namespace: _guard-oc
	oc apply -f deploy/openshift/namespace.yaml

# ── Component deploy targets ──────────────────────────────────────────────────

## Step 1 — Postgres
deploy-postgres: _guard-pg-password _guard-oc _guard-helm _deploy-namespace
	@echo "==> Deploying Postgres..."
	helm upgrade --install postgres $(CHART_POSTGRES) \
	  $(HELM_COMMON) \
	  --set image=$(IMG_POSTGRES) \
	  --set postgres.password=$(PG_PASSWORD) \
	  --wait --timeout 5m
	@echo "    Postgres ready."

## Step 2 — Neo4j (graph DB + Browser UI)
## Uses the official neo4j/neo4j Helm chart, mirroring the working "neo4j" project.
deploy-neo4j: _guard-neo4j-password _guard-oc _guard-helm
	@echo "==> Adding/updating Neo4j Helm repo..."
	helm repo add neo4j https://helm.neo4j.com/neo4j 2>/dev/null || true
	helm repo update neo4j
	$(eval OCP_DOMAIN   := $(shell oc get ingresses.config/cluster -o jsonpath='{.spec.domain}' 2>/dev/null))
	$(eval NEO4J_ROUTE_HOST := neo4j-$(NAMESPACE).$(OCP_DOMAIN))
	@echo "==> Creating neo4j-auth secret (NEO4J_AUTH=neo4j/<password>)..."
	@oc create secret generic neo4j-auth \
	  --from-literal=NEO4J_AUTH="neo4j/$(NEO4J_PASSWORD)" \
	  -n $(NAMESPACE) 2>/dev/null || \
	  oc patch secret neo4j-auth -n $(NAMESPACE) \
	    -p "{\"data\":{\"NEO4J_AUTH\":\"$$(echo -n 'neo4j/$(NEO4J_PASSWORD)' | base64)\"}}"
	@echo "==> Deploying Neo4j via official Helm chart (neo4j/neo4j 2026.5.0)..."
	helm upgrade --install neo4j neo4j/neo4j \
	  --version 2026.5.0 \
	  $(HELM_COMMON) \
	  -f $(CHART_NEO4J)/values.yaml \
	  --set "config.server\.default_advertised_address=$(NEO4J_ROUTE_HOST)" \
	  --wait --timeout 10m
	@echo "==> Creating edge-terminated HTTPS Route for Neo4j Browser..."
	@oc create route edge neo4j \
	  --service=neo4j --port=tcp-http \
	  --insecure-policy=Redirect \
	  -n $(NAMESPACE) 2>/dev/null || true
	@printf "\n    Neo4j deployed.\n"
	@printf "    Browser UI : https://$(NEO4J_ROUTE_HOST)/browser/\n"
	@printf "    Bolt URI   : neo4j://$(NEO4J_ROUTE_HOST)\n\n"

## Print the port-forward command and open Neo4j Browser locally.
## Bolt cannot be proxied through an OpenShift Route; port-forward is the
## supported access method for demo deployments.
neo4j-connect: _guard-oc
	@printf "\n==> Starting Neo4j port-forward (ctrl-c to stop)...\n"
	@printf "    Browser UI: http://localhost:7474/browser/\n"
	@printf "    Connect with: bolt://localhost:7687\n"
	@printf "    Username: neo4j\n\n"
	oc port-forward svc/neo4j 7474:7474 7687:7687 -n $(NAMESPACE)

## Step 3 — Schema bootstrap
deploy-bootstrap: _guard-pg-password _guard-neo4j-password _guard-helm
	@echo "==> Running schema bootstrap Job..."
	helm upgrade --install bootstrap $(CHART_BOOTSTRAP) \
	  $(HELM_COMMON) \
	  --set image=$(IMG_APP) \
	  --set postgres.password=$(PG_PASSWORD) \
	  --set neo4j.password=$(NEO4J_PASSWORD) \
	  --atomic --timeout 3m
	@echo "    Bootstrap complete."

## Step 4 — vLLM  (GPU required; timeout is generous for model loading)
deploy-vllm: _guard-helm
	@echo "==> Deploying vLLM..."
	helm upgrade --install vllm $(CHART_VLLM) \
	  $(HELM_COMMON) \
	  --set image=$(IMG_VLLM) \
	  --wait --timeout 15m
	@echo "    vLLM ready."

## Step 5 — FastAPI API
deploy-api: _guard-pg-password _guard-neo4j-password _guard-helm
	@echo "==> Deploying FastAPI API..."
	helm upgrade --install api $(CHART_API) \
	  $(HELM_COMMON) \
	  --set image=$(IMG_APP) \
	  --set postgres.password=$(PG_PASSWORD) \
	  --set neo4j.password=$(NEO4J_PASSWORD) \
	  --set llm.apiKey=$(OPENAI_API_KEY) \
	  --wait --timeout 3m
	@printf "    API ready. Route:\n"
	@oc get route general-sim-api -n $(NAMESPACE) \
	  -o jsonpath='    https://{.spec.host}/health{"\n"}' 2>/dev/null || true

## Step 6 — Ingestion CronJob
deploy-ingestion: _guard-pg-password _guard-neo4j-password _guard-helm
	@echo "==> Deploying ingestion CronJob..."
	helm upgrade --install ingestion $(CHART_INGESTION) \
	  $(HELM_COMMON) \
	  --set image=$(IMG_APP) \
	  --set postgres.password=$(PG_PASSWORD) \
	  --set neo4j.password=$(NEO4J_PASSWORD) \
	  --set llm.apiKey=$(OPENAI_API_KEY) \
	  --wait --timeout 2m
	@echo "    Ingestion CronJob configured."

## Full ordered deploy
deploy: _guard-pg-password _guard-neo4j-password _guard-oc _guard-helm \
        deploy-postgres deploy-neo4j deploy-bootstrap deploy-vllm \
        deploy-api deploy-ingestion
	@printf "\n==> Full deployment complete.\n"
	@printf "    Smoke test:\n"
	@printf "      ROUTE=\$$(oc get route general-sim-api -n $(NAMESPACE)"
	@printf " -o jsonpath='{.spec.host}')\n"
	@printf "      curl -s https://\$$ROUTE/health | jq .\n"
	@printf "\n    To open Neo4j Browser:\n"
	@printf "      make neo4j-connect NAMESPACE=$(NAMESPACE)\n\n"
	@printf "\n"

# ── Undeploy ──────────────────────────────────────────────────────────────────
undeploy: _guard-helm
	@echo "==> Removing Helm releases from namespace $(NAMESPACE)..."
	helm uninstall ingestion --namespace $(NAMESPACE) 2>/dev/null || true
	helm uninstall api       --namespace $(NAMESPACE) 2>/dev/null || true
	helm uninstall vllm      --namespace $(NAMESPACE) 2>/dev/null || true
	helm uninstall bootstrap --namespace $(NAMESPACE) 2>/dev/null || true
	helm uninstall neo4j     --namespace $(NAMESPACE) 2>/dev/null || true
	helm uninstall postgres  --namespace $(NAMESPACE) 2>/dev/null || true
	@echo "    Done. PVCs are NOT deleted automatically — remove manually if needed:"
	@echo "      oc delete pvc -n $(NAMESPACE) --all"

# ── Status ────────────────────────────────────────────────────────────────────
status: _guard-helm _guard-oc
	@echo "==> Helm releases in namespace $(NAMESPACE):"
	@helm list --namespace $(NAMESPACE)
	@echo ""
	@echo "==> Pod status:"
	@oc get pods -n $(NAMESPACE)

# ── Lint ──────────────────────────────────────────────────────────────────────
lint-charts: _guard-helm
	@for chart in \
	  $(CHART_POSTGRES) \
	  $(CHART_BOOTSTRAP) \
	  $(CHART_VLLM) \
	  $(CHART_API) \
	  $(CHART_INGESTION); do \
	  printf "==> Linting $$chart ...\n"; \
	  helm lint "$$chart" || exit 1; \
	done
	@echo "==> All charts passed lint."
	@echo "    (neo4j uses the official neo4j/neo4j chart — no local chart to lint)"
