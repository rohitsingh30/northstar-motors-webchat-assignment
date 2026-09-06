#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${GCP_REGION:-asia-south1}"
ZONE="${GCP_ZONE:-${REGION}-a}"
INSTANCE_NAME="${GCP_INSTANCE_NAME:-northstar-conversational-webchat}"
MACHINE_TYPE="${GCP_MACHINE_TYPE:-e2-medium}"
SECRET_NAME="${LLM_SECRET_NAME:-northstar-review-llm-api-key}"
TUNNEL_MODE="${NORTHSTAR_TUNNEL_MODE:-named}"
TUNNEL_SECRET_NAME="${CLOUDFLARE_TUNNEL_SECRET_NAME:-northstar-cloudflare-tunnel-token}"
SERVICE_ACCOUNT_ID="${GCP_SERVICE_ACCOUNT_ID:-northstar-webchat-vm}"

for command in gcloud docker tar; do
  command -v "${command}" >/dev/null || { echo "${command} is required." >&2; exit 1; }
done
if [[ -z "${PROJECT_ID}" || "${PROJECT_ID}" == "(unset)" ]]; then
  echo "Set GCP_PROJECT_ID or select a gcloud project." >&2
  exit 1
fi
if [[ -z "${LLM_API_KEY:-}" || -z "${LLM_PROVIDER_URL:-}" || -z "${LLM_MODEL:-}" ]]; then
  echo "LLM_API_KEY, LLM_PROVIDER_URL, and LLM_MODEL must be present in the environment." >&2
  exit 1
fi
if [[ "${TUNNEL_MODE}" == "named" ]]; then
  if [[ -z "${CLOUDFLARE_TUNNEL_TOKEN:-}" || -z "${NORTHSTAR_PUBLIC_ORIGIN:-}" ]]; then
    echo "Named tunnel deployment requires CLOUDFLARE_TUNNEL_TOKEN and NORTHSTAR_PUBLIC_ORIGIN." >&2
    exit 1
  fi
elif [[ "${TUNNEL_MODE}" != "quick" ]]; then
  echo "NORTHSTAR_TUNNEL_MODE must be 'named' or 'quick'." >&2
  exit 1
fi

gcloud services enable compute.googleapis.com secretmanager.googleapis.com storage.googleapis.com \
  --project "${PROJECT_ID}"

if ! gcloud secrets describe "${SECRET_NAME}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud secrets create "${SECRET_NAME}" --replication-policy=automatic --project "${PROJECT_ID}"
fi
printf %s "${LLM_API_KEY}" \
  | gcloud secrets versions add "${SECRET_NAME}" --data-file=- --project "${PROJECT_ID}" >/dev/null

if [[ "${TUNNEL_MODE}" == "named" ]]; then
  if ! gcloud secrets describe "${TUNNEL_SECRET_NAME}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    gcloud secrets create "${TUNNEL_SECRET_NAME}" \
      --replication-policy=automatic --project "${PROJECT_ID}"
  fi
  printf %s "${CLOUDFLARE_TUNNEL_TOKEN}" \
    | gcloud secrets versions add "${TUNNEL_SECRET_NAME}" \
      --data-file=- --project "${PROJECT_ID}" >/dev/null
fi

compute_identity="${SERVICE_ACCOUNT_ID}@${PROJECT_ID}.iam.gserviceaccount.com"
if ! gcloud iam service-accounts describe "${compute_identity}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${SERVICE_ACCOUNT_ID}" \
    --display-name "Northstar reviewer VM" \
    --project "${PROJECT_ID}"
fi
gcloud secrets add-iam-policy-binding "${SECRET_NAME}" \
  --member "serviceAccount:${compute_identity}" \
  --role roles/secretmanager.secretAccessor \
  --project "${PROJECT_ID}" >/dev/null
if [[ "${TUNNEL_MODE}" == "named" ]]; then
  gcloud secrets add-iam-policy-binding "${TUNNEL_SECRET_NAME}" \
    --member "serviceAccount:${compute_identity}" \
    --role roles/secretmanager.secretAccessor \
    --project "${PROJECT_ID}" >/dev/null
fi

if ! gcloud compute instances describe "${INSTANCE_NAME}" --zone "${ZONE}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud compute instances create "${INSTANCE_NAME}" \
    --project "${PROJECT_ID}" \
    --zone "${ZONE}" \
    --machine-type "${MACHINE_TYPE}" \
    --image-family ubuntu-2404-lts-amd64 \
    --image-project ubuntu-os-cloud \
    --boot-disk-size 30GB \
    --service-account "${compute_identity}" \
    --scopes cloud-platform \
    --metadata startup-script='#!/usr/bin/env bash
set -e
apt-get update
apt-get install -y docker.io docker-compose-v2
systemctl enable --now docker'
fi

for _ in {1..60}; do
  if gcloud compute ssh "${INSTANCE_NAME}" --zone "${ZONE}" --project "${PROJECT_ID}" \
      --command 'docker compose version >/dev/null 2>&1' >/dev/null 2>&1; then
    break
  fi
  sleep 5
done

archive="$(mktemp -t northstar-review.XXXXXX.tar.gz)"
bucket_name="${PROJECT_ID}-northstar-$(date +%s)"
deployment_object="gs://${bucket_name}/northstar-review.tar.gz"
cleanup() {
  rm -f "${archive}"
  gcloud storage rm "${deployment_object}" --quiet >/dev/null 2>&1 || true
  gcloud storage buckets delete "gs://${bucket_name}" --quiet >/dev/null 2>&1 || true
}
trap cleanup EXIT
COPYFILE_DISABLE=1 tar -C "${ROOT_DIR}" --no-xattrs \
  --exclude .git \
  --exclude .env \
  --exclude .DS_Store \
  --exclude '._*' \
  --exclude .ruff_cache \
  --exclude '**/__pycache__' \
  --exclude '**/.pytest_cache' \
  --exclude '**/.venv' \
  --exclude '**/node_modules' \
  -czf "${archive}" .

gcloud storage buckets create "gs://${bucket_name}" \
  --location "${REGION}" --uniform-bucket-level-access --project "${PROJECT_ID}" --quiet
gcloud storage buckets add-iam-policy-binding "gs://${bucket_name}" \
  --member "serviceAccount:${compute_identity}" \
  --role roles/storage.objectViewer --quiet >/dev/null
gcloud storage cp "${archive}" "${deployment_object}" --quiet

gcloud compute ssh "${INSTANCE_NAME}" --zone "${ZONE}" --project "${PROJECT_ID}" --command \
  "set -e && access_token=\$(curl -fsS -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"access_token\"])') && curl -fsS -H \"Authorization: Bearer \${access_token}\" 'https://storage.googleapis.com/${bucket_name}/northstar-review.tar.gz' -o /tmp/northstar-review.tar.gz && release=/opt/northstar/releases/\$(date +%Y%m%d%H%M%S) && sudo mkdir -p \"\${release}\" && sudo tar -xzf /tmp/northstar-review.tar.gz -C \"\${release}\" && sudo ln -sfn \"\${release}\" /opt/northstar/current && sudo chmod +x /opt/northstar/current/deployment/gcp/start-review.sh && sudo env LLM_PROVIDER_URL='${LLM_PROVIDER_URL}' LLM_MODEL='${LLM_MODEL}' LLM_SECRET_NAME='${SECRET_NAME}' NORTHSTAR_TUNNEL_MODE='${TUNNEL_MODE}' NORTHSTAR_PUBLIC_ORIGIN='${NORTHSTAR_PUBLIC_ORIGIN:-}' CLOUDFLARE_TUNNEL_SECRET_NAME='${TUNNEL_SECRET_NAME}' /opt/northstar/current/deployment/gcp/start-review.sh"
