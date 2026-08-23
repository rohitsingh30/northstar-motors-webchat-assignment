#!/usr/bin/env bash
set -euo pipefail

DEPLOYMENT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ID="$(curl -fsS -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/project/project-id)"
SECRET_NAME="${LLM_SECRET_NAME:-northstar-review-llm-api-key}"
TUNNEL_MODE="${NORTHSTAR_TUNNEL_MODE:-named}"

if [[ -z "${LLM_PROVIDER_URL:-}" || -z "${LLM_MODEL:-}" ]]; then
  echo "LLM_PROVIDER_URL and LLM_MODEL are required." >&2
  exit 1
fi

access_token="$({ curl -fsS -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token; } \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])')"
export LLM_API_KEY="$({ curl -fsS \
  -H "Authorization: Bearer ${access_token}" \
  "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${SECRET_NAME}/versions/latest:access"; } \
  | python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["payload"]["data"]).decode())')"

export NORTHSTAR_API_KEY="$(openssl rand -hex 24)"
export NORTHSTAR_ADMIN_KEY="$(openssl rand -hex 24)"
compose=(docker compose -f "${DEPLOYMENT_DIR}/compose.yaml")

case "${TUNNEL_MODE}" in
  named)
    if [[ -z "${NORTHSTAR_PUBLIC_ORIGIN:-}" ]]; then
      echo "NORTHSTAR_PUBLIC_ORIGIN is required for a named tunnel." >&2
      exit 1
    fi
    if [[ ! "${NORTHSTAR_PUBLIC_ORIGIN}" =~ ^https://[^/]+/?$ ]]; then
      echo "NORTHSTAR_PUBLIC_ORIGIN must be an HTTPS origin without a path." >&2
      exit 1
    fi
    tunnel_secret_name="${CLOUDFLARE_TUNNEL_SECRET_NAME:-northstar-cloudflare-tunnel-token}"
    export CLOUDFLARE_TUNNEL_TOKEN="$({ curl -fsS \
      -H "Authorization: Bearer ${access_token}" \
      "https://secretmanager.googleapis.com/v1/projects/${PROJECT_ID}/secrets/${tunnel_secret_name}/versions/latest:access"; } \
      | python3 -c 'import base64,json,sys; print(base64.b64decode(json.load(sys.stdin)["payload"]["data"]).decode())')"
    export REVIEWER_ORIGIN="${NORTHSTAR_PUBLIC_ORIGIN%/}"
    "${compose[@]}" --profile named-tunnel up -d --build --remove-orphans
    reviewer_origin="${REVIEWER_ORIGIN}"
    ;;
  quick)
    export CLOUDFLARE_TUNNEL_TOKEN="unused-in-quick-tunnel-mode"
    export REVIEWER_ORIGIN="https://pending.invalid"
    "${compose[@]}" --profile quick-tunnel up -d --build --remove-orphans

    reviewer_origin=""
    for _ in {1..60}; do
      reviewer_origin="$("${compose[@]}" logs cloudflared-quick 2>&1 \
        | grep -Eo 'https://[-a-z0-9]+\.trycloudflare\.com' \
        | tail -1 || true)"
      [[ -n "${reviewer_origin}" ]] && break
      sleep 2
    done

    if [[ -z "${reviewer_origin}" ]]; then
      echo "The Cloudflare review URL was not created. Inspect the cloudflared-quick logs." >&2
      exit 1
    fi

    export REVIEWER_ORIGIN="${reviewer_origin}"
    "${compose[@]}" up -d --no-deps --force-recreate webchat-service
    ;;
  *)
    echo "NORTHSTAR_TUNNEL_MODE must be 'named' or 'quick'." >&2
    exit 1
    ;;
esac
unset access_token

for _ in {1..30}; do
  if curl -fsS "${reviewer_origin}/_health" >/dev/null; then
    printf 'Northstar review URL (%s tunnel): %s\n' "${TUNNEL_MODE}" "${reviewer_origin}"
    exit 0
  fi
  sleep 2
done

echo "The review URL was created but the application did not become healthy." >&2
exit 1
