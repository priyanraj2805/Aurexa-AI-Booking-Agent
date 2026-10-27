# DotStark Booking Agent

DotStark is a multi-tenant appointment-booking platform. It combines a customer chat widget, a tenant administration dashboard, a FastAPI booking API, a Super Admin control plane, and a WhatsApp transport gateway.

## Start here

- **Developer handover:** [docs/DEVELOPER_HANDOVER.md](docs/DEVELOPER_HANDOVER.md) — architecture, component ownership, development workflow, tests, deployment notes, and operational cautions.
- **Documentation hub:** [docs/index.md](docs/index.md)
- **Local Docker setup:** [SETUP.md](SETUP.md)
- **Environment variable reference:** [docs/reference/environment-vars.md](docs/reference/environment-vars.md)
- **Production deployment notes:** [docs/deployment/production.md](docs/deployment/production.md)

## Quick local start (Windows PowerShell)

1. Install Docker Desktop and make sure its engine is running.
2. Create local environment files only when they do not already exist:

   ```powershell
   if (-not (Test-Path .env)) { Copy-Item .env.example .env }
   if (-not (Test-Path backend\.env.docker)) { Copy-Item backend\.env.docker.example backend\.env.docker }
   if (-not (Test-Path backend-super\.env.docker)) { Copy-Item backend-super\.env.docker.example backend-super\.env.docker }
   ```

3. Fill in the required local values in those files, and make the root and tenant API `WA_GATEWAY_SERVICE_KEY` values match. Do not put credentials in source files or documentation.
4. Start the stack:

   ```powershell
   docker compose up --build
   ```

The Compose stack publishes the tenant API on `http://localhost:8005`, tenant dashboard on `http://localhost:3002`, Super Admin API on `http://localhost:8006`, Super Admin UI on `http://localhost:3003`, and the Nginx tenant gateway on `http://localhost:8080`. PostgreSQL, Redis, and the WhatsApp gateway are internal Compose services.

For the full setup, including Cloudflare tunnel behavior, see [SETUP.md](SETUP.md). Do not use the quick-tunnel configuration as a production deployment strategy.

## Repository map

| Path | Responsibility |
|---|---|
| `backend/` | Tenant-facing FastAPI application, booking engine, API, database models and migrations, and the served widget bundle |
| `backend-super/` | Separate Super Admin FastAPI control plane |
| `frontend/admin-panel/` | Tenant dashboard (Next.js) |
| `frontend/super-admin/` | Super Admin dashboard (Next.js) |
| `frontend/chat-widget/` | Widget source, build, and browser tests |
| `wa-gateway/` | Internal WhatsApp Web/QR transport service |
| `gateway/` | Nginx routing for the tenant dashboard and API |
| `docs/` | Product, architecture, deployment, and operator documentation |

This repository does not have a single root-level application test command. Component-specific test and build commands are documented in the [developer handover](docs/DEVELOPER_HANDOVER.md).
