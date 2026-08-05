#!/usr/bin/env bash

set -euo pipefail

TOKEN=$(curl -sf -X POST http://localhost:8081/default/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=dev-client" \
  | jq -r ".access_token")

echo "export TOKEN='${TOKEN}'"
echo ""
echo "# Test RLS:"
echo "curl -s http://localhost:3111/endpoint_participante_listagem -H \"Authorization: Bearer ${TOKEN}\""
