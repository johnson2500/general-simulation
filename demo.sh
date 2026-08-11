#!/usr/bin/env bash
set -euo pipefail

API_BASE="https://general-sim-api-general-sim.apps.ocp.sandoval.lab"
SCENARIO_ID="${1:-shipping-la-closure-001}"
QUESTION="${2:-Port of Los Angeles is closed due to a strike. What vessels and shipments are affected and what should we do?}"

echo "==> Health check"
curl -sk "${API_BASE}/health" | python3 -m json.tool

echo ""
echo "==> Submitting query"
echo "    scenario_id : ${SCENARIO_ID}"
echo "    question    : ${QUESTION}"
echo ""

curl -sk -X POST "${API_BASE}/query" \
  -H "Content-Type: application/json" \
  -d "{\"scenario_id\": \"${SCENARIO_ID}\", \"question\": \"${QUESTION}\"}" \
  | python3 -m json.tool
