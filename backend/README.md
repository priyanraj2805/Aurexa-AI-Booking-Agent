# Tenant API

The `backend/` directory contains the tenant-facing FastAPI application: chat/booking orchestration, organization and staff administration, availability, integrations, and the API used by the tenant dashboard and customer widget.

## Run the full application

From the repository root, follow [SETUP.md](../SETUP.md) or the [developer handover](../docs/DEVELOPER_HANDOVER.md). The Docker Compose setup also starts `backend-super`, both frontends, PostgreSQL, Redis, the Nginx gateway, and the WhatsApp gateway.

The tenant API is available at `http://localhost:8005`; interactive API documentation is at `/docs`. In the Compose container, `docker-entrypoint.sh` invokes `init_db.py` before starting the API.

## Key directories

| Path | Responsibility |
|---|---|
| `app/api/v1/endpoints/` | HTTP endpoints |
| `app/engine/` | Conversation, booking, scheduling, and state orchestration |
| `app/crud/` | Database access |
| `app/models/`, `app/schemas/` | Persistence models and data contracts |
| `app/core/` | Configuration and shared runtime/security components |
| `alembic/versions/` | Database schema migrations |
| `static/widget/` | Widget bundle served by this API |
| `tests/` | Tenant backend tests |

## Test

Run from this directory:

```powershell
python -m pytest
```

Tests that require external services or a configured database may need the matching local environment. Do not run state-changing tests against production.

## Bootstrap warning

The database initializer contains fixed bootstrap account and API-key setup. Do not use credentials copied from historical documentation. Replace or rotate bootstrap access through an approved process before exposing a deployment, and keep local credentials out of source control.

## Related documentation

- [Developer handover](../docs/DEVELOPER_HANDOVER.md)
- [Environment variable reference](../docs/reference/environment-vars.md)
- [API overview](../docs/api/overview.md) (confirm exact contracts against the live OpenAPI schema)
- [Database architecture](../docs/architecture/database.md) (confirm exact schema against models and migrations)
