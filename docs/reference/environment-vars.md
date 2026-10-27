# Environment and Secrets Reference

This guide covers where the current Compose stack gets configuration and how to manage it safely. The example files in the repository are the source of truth for the full variable list; application settings are defined in each service's configuration module.

## Local configuration files

| Runtime file | Copy from | Used by |
|---|---|---|
| `.env` | `.env.example` | Docker Compose interpolation, PostgreSQL initialization, and internal WhatsApp gateway key |
| `backend/.env.docker` | `backend/.env.docker.example` | Tenant FastAPI application |
| `backend-super/.env.docker` | `backend-super/.env.docker.example` | Super Admin FastAPI application |

Create each file only if it does not already exist. Keep all runtime values local or in the approved secret manager; do not commit them.

## Compose-level variables

The root `.env.example` supplies the database values used to initialize the PostgreSQL container:

| Variable | Purpose |
|---|---|
| `POSTGRES_USER` | Initial PostgreSQL administrator username |
| `POSTGRES_PASSWORD` | Initial PostgreSQL administrator password |
| `POSTGRES_DB` | Initial database name |
| `WA_GATEWAY_SERVICE_KEY` | Shared secret used by the tenant API and internal WhatsApp gateway |
| `CLOUDFLARE_TUNNEL_TOKEN_BACKEND`, `CLOUDFLARE_TUNNEL_TOKEN_ADMIN` | Template values for optional named-tunnel setup; the current Compose commands use temporary quick tunnels by default |

Set `WA_GATEWAY_SERVICE_KEY` to the same locally generated value in the root `.env` and `backend/.env.docker`. Do not reuse a real production key for development. The example file leaves secrets blank intentionally.

## Tenant API settings

The tenant API reads its configuration through `backend/app/core/config.py`. Use `backend/.env.docker.example` as the authoritative key list. It includes settings for:

- PostgreSQL connection and pool behavior
- JWT/authentication and application security
- LLM provider/model selection
- SMTP and email delivery
- frontend/public API URLs and widget configuration
- calendar providers and OAuth callbacks
- WhatsApp gateway, QR, and optional Meta Cloud API behavior
- Razorpay/Cashfree and other optional payment features
- feature flags, observability, and background/runtime limits

Only configure integrations that are intended to be enabled in the environment. Keep callback URLs, trusted origins, and public API URLs consistent with the deployed domain and reverse-proxy topology.

## Super Admin API settings

The Super Admin API reads settings through `backend-super/app/core/config.py`; use `backend-super/.env.docker.example` as its authoritative key list. It has a separate platform database connection and a distinct read-only tenant-data database connection, plus its own authentication, security, bridge, and telemetry settings.

Do not point the read-only tenant connection at a writable role. Do not copy the tenant API's JWT secret into the Super Admin service; keep the two trust domains separate.

## Frontend and gateway configuration

Docker Compose sets the server-side `INTERNAL_API_URL` for each Next.js app:

- Tenant dashboard → `http://backend:8005`
- Super Admin UI → `http://backend-super:8006`

These are internal Compose network URLs. The tenant Nginx gateway and the external browser use different public-facing URLs; do not replace the internal addresses with `localhost` inside containers.

## Generate local secrets

Generate a different random secret for each environment and service. For example, with Python installed:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Use the resulting value for a secret setting such as a JWT or service-to-service key, according to that setting's requirements. Store production credentials only in the approved secret manager. Never paste secrets into source code, docs, screenshots, issue trackers, or logs.

## Production and rotation checklist

- Replace all example/blank values before startup; verify the target environment's required settings.
- Rotate credentials that have ever been checked into source, copied into docs, or shared outside the approved secret store.
- Use separate keys for tenant API, Super Admin API, gateway authentication, and provider integrations.
- Configure HTTPS, stable OAuth callback URLs, allowed frontend origins, and the intended public API base.
- Review bootstrap initialization in `backend/init_db.py`; fixed bootstrap account/API-key setup must not be treated as production identity provisioning.
- Back up and protect database credentials, Cloudflare credentials, WhatsApp auth state, and database dumps as sensitive data.

For service startup and deployment context, see the [developer handover](../DEVELOPER_HANDOVER.md) and [local setup guide](../../SETUP.md).
