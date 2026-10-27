# Developer Handover

> **Audience:** Developers taking over implementation, support, or deployment of this repository.
>
> **Purpose:** A code-oriented map of the current system and the safest way to work on it. This guide describes the checked-in source and Compose wiring; where older documents conflict with these, verify behavior against the code and live API schema.

## 1. Product at a glance

DotStark is a multi-tenant booking platform. Customers use a chat widget or WhatsApp to request an appointment. The tenant dashboard configures the organization and manages bookings and operations. A separate Super Admin application manages platform-level operations.

The booking engine follows a “deterministic business logic, AI-assisted conversation” approach: the backend owns session state, validation, availability, and booking writes; language-model functionality is an integration in the conversation flow, not the source of truth for whether a booking is valid.

## 2. System topology

```text
Customer browser ── embedded widget ─────────┐
Customer WhatsApp ── WhatsApp gateway ───────┤
                                             ▼
Tenant browser ── tenant dashboard ── Nginx gateway ── tenant API
                                             │              │
                                             │              ├── PostgreSQL
                                             │              ├── Redis
                                             │              ├── LLM / email / calendar / payment providers
                                             │              └── WhatsApp gateway (internal HTTP)
                                             │
Platform operator ── Super Admin UI ── Super Admin API ── PostgreSQL
                                                   └────── read-only tenant-data connection
```

The Compose file is the best source for local service wiring: [docker-compose.yml](../docker-compose.yml). Nginx routes `/api/*` and `/health` to the tenant API and other paths to the tenant dashboard; see [gateway/nginx.conf](../gateway/nginx.conf). The Super Admin UI/API are separate services, not routes on the tenant gateway.

### Services and local ports

| Service | Source | Published port | Responsibility |
|---|---|---:|---|
| PostgreSQL | Compose image | Internal only | Tenant and platform persistence |
| Redis | Compose image | Internal only | Supporting cache/queue service; confirm current feature use before changing or removing it |
| Tenant API | `backend/` | `8005` | Booking, tenant auth/admin, widget, integrations, payments, and WhatsApp-facing API |
| Tenant dashboard | `frontend/admin-panel/` | `3002` | Organization administration |
| Super Admin API | `backend-super/` | `8006` | Platform operations, support, audit, billing, approvals, and impersonation |
| Super Admin UI | `frontend/super-admin/` | `3003` | Platform operator interface |
| Tenant Nginx gateway | `gateway/` | `8080` (container port 80) | Tenant dashboard/API single-origin routing |
| WhatsApp gateway | `wa-gateway/` | Internal `3100` | Baileys-based WhatsApp session and message transport |
| Cloudflare quick tunnels | Compose services | Outbound tunnels | Temporary public URLs for development/demo use |

The port table describes this repository's current Compose configuration. Older architecture documents may show previous ports.

## 3. Repository map and ownership

### Tenant API: `backend/`

- `app/main.py`: FastAPI app, middleware, health endpoint, API router, static widget mounting, and application lifespan tasks.
- `app/api/v1/api.py`: central router registry. Add new tenant API routers here.
- `app/api/v1/endpoints/`: HTTP boundary. Endpoint modules cover auth, chat, dashboard, slots, catalog, staff, integrations, payments, notifications, WhatsApp, and related workflows.
- `app/engine/`: conversation and booking orchestration, state merging/recovery, validation, scheduling policy, slot selection, and WhatsApp-specific presentation/guards.
- `app/services/`: integration and cross-cutting business services.
- `app/crud/`: database operations.
- `app/models/` and `app/schemas/`: SQLAlchemy persistence types and Pydantic API/data contracts.
- `app/core/`: configuration, authentication/security, background tasks, logging, rate limits, and shared runtime behavior.
- `alembic/versions/`: tenant/platform schema migration history.
- `static/widget/`: widget bundle served by the tenant API at `/static/widget/`.
- `tests/`: API contracts, engine, unit, runtime, data compatibility, and WhatsApp tests.
- `init_db.py`: runs Alembic migrations during container startup and performs initial database setup.

### Super Admin API: `backend-super/`

- `app/main.py`: independent FastAPI app, health checks, CORS/CSRF middleware, and router mounting.
- `app/api/v1/api.py`: platform API router registry.
- `app/api/v1/endpoints/`: platform auth, tenant lifecycle, approvals, plans/billing, reports, audit, incidents, support, impersonation, and security APIs.
- `app/core/`: platform security/RBAC, audit, billing, tenant bridge, notifications, PII controls, and security policies.
- `app/db/database.py`: distinct database sessions for platform writes and read-only tenant-data reads.

The database module documents a deliberate boundary: the platform admin connection is for the `super_admin` schema; tenant data reads use a separate read-only connection. Preserve this boundary when adding platform features.

### Frontends and gateway

- `frontend/admin-panel/`: tenant Next.js application. API/auth utilities are in `src/lib/`; route screens are under `src/app/`.
- `frontend/super-admin/`: platform operator Next.js application. Its API/auth utilities are in `src/lib/`.
- `frontend/chat-widget/`: source widget and Playwright browser tests. `npm run deploy` builds the widget and copies the output to `backend/static/widget/widget.js`; build/deploy this after changing widget source.
- `gateway/nginx.conf`: tenant single-origin routing and proxy settings.

### WhatsApp gateway

`wa-gateway/src/` implements the internal Express/Baileys service: process entry point, session lifecycle, auth persistence, inbound handling, outbound delivery, watchdog/recovery, and internal session routes. The backend is its client; it is not exposed as a public browser API. Persistent WhatsApp auth is stored in the Compose `wa_auth` volume.

## 4. Important runtime flows

### Customer booking

1. The widget sends a request to the tenant API with organization/widget credentials and an optional session identifier.
2. The chat endpoint resolves the tenant and loads or creates conversation state.
3. The conversation engine processes user input, applies business validation, updates state, and chooses the next action/widget directive.
4. Availability and booking persistence are handled by backend business/data-access code, not by the browser.
5. The backend returns a response/action for the widget and may trigger configured notifications or external integrations.

Use the live OpenAPI schema at `/docs` and `/api/v1/openapi.json` for the exact request and response shape. The current source registers API routers in `backend/app/api/v1/api.py`.

### Tenant dashboard

The browser UI calls the tenant API. In Docker, the dashboard's server-side `INTERNAL_API_URL` points to `http://backend:8005`; the browser-facing gateway routes `/api/` to the same API. Check the frontend API helper and auth context before changing token/cookie behavior.

### Super Admin operations

The Super Admin UI calls the separate Super Admin API. That service has a platform database session and a separate read-only tenant database session. Some cross-plane capabilities use the internal bridge and are gated by configuration; do not expose internal routes as public tenant APIs.

### Widget delivery

The source project is `frontend/chat-widget/`; the tenant API serves the deployed bundle under `/static/widget/`. If widget behavior changes, build and deploy it using the package script (see below), then verify the served file and a real embed. Editing only the built bundle risks losing the change on the next build.

### WhatsApp

The tenant API coordinates WhatsApp behavior with the internal gateway. Gateway authentication and the persisted Baileys state are operational dependencies. The backend contains process-local WhatsApp deduplication/locking state and explicitly warns about multi-worker operation; keep the tenant API single-process unless that coordination is deliberately redesigned and validated.

## 5. Data and migrations

- PostgreSQL is the system of record. The tenant API uses SQLAlchemy async sessions; schema evolution is under `backend/alembic/`.
- The tenant API startup entrypoint runs `python init_db.py` before starting FastAPI. That initializer invokes Alembic and performs initial setup.
- The Super Admin API uses the same PostgreSQL service but separate configured connections and schema responsibilities. Do not grant the tenant read-only connection write access.
- Do not edit the database manually as a substitute for a migration. Add a revision, review generated SQL and constraints, and test both upgrade behavior and affected application queries.
- Protect tenant boundaries: every tenant-scoped read/write must be scoped to the authenticated/resolved organization, including background tasks and bulk operations.
- Before applying migrations to production, back up the database, verify current Alembic heads, and have a tested rollback/recovery plan. Some changes are not safely reversible by a simple downgrade.
- Compose persists PostgreSQL, uploaded organization logos, and WhatsApp auth state in named volumes. `docker compose down -v` deletes these volumes; do not run it against data you need.

## 6. Configuration and secrets

Templates are the starting point; runtime files are local and must not be committed:

| File | Purpose |
|---|---|
| `.env.example` → `.env` | Compose-level database, WhatsApp gateway shared key, and tunnel interpolation |
| `backend/.env.docker.example` → `backend/.env.docker` | Tenant API configuration |
| `backend-super/.env.docker.example` → `backend-super/.env.docker` | Super Admin API configuration |

Read [docs/reference/environment-vars.md](reference/environment-vars.md) and the component templates for the actual keys and required integrations. A configured LLM, email provider, calendar, payment processor, or WhatsApp service may be necessary for its corresponding workflow; do not assume every optional integration is active in a local environment.

**Secret handling:** never copy values from local `.env` files, `deploy-secrets/`, Cloudflare credential files, database dumps, archives, or WhatsApp auth data into docs, issues, screenshots, or commits. Use the approved secret store and rotate credentials if they have been shared.

**Bootstrap warning:** the tenant database initializer currently contains fixed bootstrap account/API-key setup. Do not use historical README credentials or expose a deployment before the bootstrap path and credentials have been replaced/rotated through an approved process. The credential values are intentionally not repeated in this guide.

## 7. Local development

### Recommended full-stack route: Docker Compose

From the repository root in PowerShell:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
if (-not (Test-Path backend\.env.docker)) { Copy-Item backend\.env.docker.example backend\.env.docker }
if (-not (Test-Path backend-super\.env.docker)) { Copy-Item backend-super\.env.docker.example backend-super\.env.docker }
# Edit the three local files and supply required values.
docker compose up --build
```

Set the root `WA_GATEWAY_SERVICE_KEY` in `.env` to the same value configured for the tenant API in `backend/.env.docker`. If any destination file already exists, preserve it and edit it rather than overwriting local credentials. The backend container runs migrations on startup. `.\start.ps1` is an alternative that starts Compose in detached mode and prints the tenant API/admin quick-tunnel URLs when available. Quick-tunnel hostnames are temporary and can rotate.

Useful operations:

```powershell
docker compose ps
docker compose logs -f backend
docker compose logs -f backend-super
docker compose logs -f wa-gateway
docker compose down
```

For a development service URL, use the direct port; `localhost:8080` is the Nginx tenant gateway. The Super Admin UI is on `localhost:3003` and its API is on `localhost:8006`.

### Component-specific commands

Run package commands from the indicated directory and install dependencies with the committed lockfile when needed.

| Component | Build/check/test commands |
|---|---|
| Tenant API | `cd backend`; `python -m pytest` |
| Tenant dashboard | `cd frontend/admin-panel`; `npm run lint`; `npm run build` |
| Super Admin UI | `cd frontend/super-admin`; `npm run lint`; `npm run build` |
| WhatsApp gateway | `cd wa-gateway`; `npm test` |
| Widget | `cd frontend/chat-widget`; `npm run build`; `npm run test:e2e` |
| Super Admin E2E scripts | See [scripts/e2e/README.md](../scripts/e2e/README.md); these require a running backend and a configured database and can alter test state |

The widget browser tests require Playwright's browser runtime. The Super Admin E2E scripts are integration tests, not an isolated unit-test command. Do not run any state-changing integration suite against production data.

## 8. Change workflow

1. **Identify the owning service.** Tenant booking changes usually belong in `backend/`; platform operator changes belong in `backend-super/`; UI changes belong in the corresponding frontend; WhatsApp transport changes belong in `wa-gateway/`.
2. **Trace the existing contract.** Read the endpoint, schema, service/engine, persistence code, and UI/API caller. Prefer the live API schema over old static endpoint tables.
3. **Keep the tenant boundary.** Validate organization identity at the API boundary and retain organization scoping in database operations.
4. **For schema changes,** add an Alembic revision in `backend/alembic/versions/`, test it against a disposable database, and update any data-compatibility tests or documentation affected by the new shape.
5. **For widget source changes,** build/deploy from `frontend/chat-widget/` so `backend/static/widget/widget.js` stays in sync.
6. **Test at the changed layer,** then run the nearest integration flow if the change crosses services. Record any environment-dependent checks that could not be run.
7. **Review `git status` before commit.** Local env files, generated assets, build output, dumps, archives, and unrelated working-tree edits must not be swept into a change.

## 9. Validation and operational checks

- Tenant API health: `GET /health` on port `8005`; this reports dependency health.
- Super Admin health: `GET /health` on port `8006`; it checks both database pools and reports feature gates.
- Tenant API docs: `/docs` or `/api/v1/openapi.json` on port `8005`.
- Nginx tenant gateway logs include request/upstream timing; use them to distinguish proxy delay from application delay.
- When debugging booking issues, correlate the request/session identifiers in logs before inspecting database rows. Avoid logging customer PII or secrets.
- For WhatsApp failures, inspect backend and gateway logs together, then verify the gateway health endpoint from within the Compose network and the persisted auth volume.
- For deploys, verify migrations, health of both APIs, login, one tenant-scoped read, a booking/widget journey, and any changed integration before routing real traffic.

## 10. Known handover cautions

1. **Some checked-in documentation is historical.** In particular, older architecture/API/deployment documents may state obsolete ports, module names, or configuration. The Compose file and currently registered routers are the runtime wiring references; update the affected doc when behavior changes.
2. **There is no single root test command.** Run each changed service's own lint/build/test commands and the appropriate integration checks.
3. **Bootstrap credentials are source-defined.** The initializer must be treated as a production readiness concern; do not republish existing credential strings.
4. **Tenant WhatsApp coordination has process-local state.** Do not increase Uvicorn worker count without replacing/verifying the locking and deduplication design.
5. **Quick tunnels are for temporary access.** They can rotate and are not a stable production domain or OAuth redirect configuration.
6. **Large generated/local files may be present in a working copy.** Check status and ignore rules before packaging or publishing anything.

## 11. Documentation map

- [Product and booking flow](features/booking-flow.md)
- [Time slots](features/time-slots.md)
- [OTP verification](features/otp-verification.md)
- [API overview](api/overview.md) (verify exact endpoint details against the live OpenAPI schema)
- [Database architecture](architecture/database.md) (verify exact schema against models/migrations)
- [Environment variables](reference/environment-vars.md)
- [Troubleshooting](reference/troubleshooting.md)
- [Docker setup](../SETUP.md)
- [Production deployment](deployment/production.md)

When changing behavior, update the relevant document and this handover map if component ownership, startup, data boundaries, or operating procedures change.
