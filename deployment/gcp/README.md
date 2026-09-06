# Google Cloud private demo deployment

This deployment keeps the existing three-service architecture and SQLite volumes on one Compute
Engine VM. Only the Nginx gateway is public through Cloudflare; service ports, protected values,
and model credentials are not exposed.

The deployment-ready default is a Cloudflare named tunnel with an operator-supplied permanent
hostname. No demo hostname is embedded in the repository. A temporary Quick Tunnel remains
available only when explicitly requested for local review.

The dealership seed and write semantics are unchanged; the current platform image includes the
worktree's additive public inventory-query filters documented in the integration guide. The
editable dealership website is the widget host and contains the vehicle-modal lifecycle bridge.
Nginx rewrites local development URLs in responses so the browser uses the public same-origin
`/api` and `/widget` routes without exposing the internal service ports.

## Defaults

- Region: `asia-south1`
- VM: `e2-medium`, Ubuntu 24.04, 30 GB disk
- Daily AI-backed turns: 250
- Concurrent AI-backed turns: 3
- Per-client gateway rate: 12 turns/minute
- Per-client application mutation rate: 30 requests/minute
- Conversation retention: 7 days

All values can be overridden with the corresponding environment variables in `compose.yaml`.

## Deploy

### Stable hostname (recommended)

Create a remotely managed Cloudflare tunnel and configure its public hostname to use this service:

```text
http://gateway:8080
```

Copy the tunnel token, choose the HTTPS hostname attached to that tunnel, and add these values to
the uncommitted root `.env`:

```dotenv
NORTHSTAR_TUNNEL_MODE=named
NORTHSTAR_PUBLIC_ORIGIN=https://chat.example.com
CLOUDFLARE_TUNNEL_TOKEN=the-connector-token-from-cloudflare
```

`deploy.sh` stores the connector token in Google Secret Manager. It is not included in the release
archive or passed in the remote deployment command.

Authenticate `gcloud`, select the credit-bearing project, and load `.env` into your shell without
printing it. Then run:

```bash
set -a
source .env
set +a
./deployment/gcp/deploy.sh
```

The script enables Compute Engine and Secret Manager, stores a new version of the model key, creates
the VM if needed, uploads the working tree without `.env` or `.git`, and prints the private demo URL.

### Temporary review URL

If a Cloudflare hostname is not available, explicitly select the non-stable Quick Tunnel mode:

```bash
export NORTHSTAR_TUNNEL_MODE=quick
./deployment/gcp/deploy.sh
```

The generated `trycloudflare.com` URL lasts only while that exact tunnel container is running.
Restarting or recreating it may invalidate previously shared URLs.

## Operations

Inspect the stack:

```bash
gcloud compute ssh northstar-review --zone asia-south1-a \
  --command 'cd /opt/northstar/current/deployment/gcp && docker compose ps'
```

Inspect the active tunnel logs with one of:

```bash
docker compose logs cloudflared-named
docker compose logs cloudflared-quick
```

After review, delete the VM from the Google Cloud console and disable or rotate the dedicated model
key. Delete the Secret Manager secret if it is no longer needed.
