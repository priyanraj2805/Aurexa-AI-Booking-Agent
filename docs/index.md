# DotStark Booking Agent Documentation

Use the [Developer Handover](DEVELOPER_HANDOVER.md) as the current starting point for understanding the repository, running components, testing changes, and safely taking over development.

## Choose a path

| Audience | Start here |
|---|---|
| New developer | [Developer Handover](DEVELOPER_HANDOVER.md), then [Windows/Docker setup](../SETUP.md) |
| Backend developer | [Developer Handover](DEVELOPER_HANDOVER.md#repository-map-and-ownership), [API overview](api/overview.md), [database architecture](architecture/database.md) |
| Frontend developer | [Developer Handover](DEVELOPER_HANDOVER.md#frontends-and-gateway) |
| Operator | [Production deployment](deployment/production.md), [environment variables](reference/environment-vars.md), [troubleshooting](reference/troubleshooting.md) |
| Product/support | [Product overview](PRODUCT_DOCUMENT.md), [booking flow](features/booking-flow.md), [Super Admin handbook](reference/superadmin-user-handbook.md) |

## Reference documents

### Architecture

- [System overview](architecture/system-overview.md)
- [Developer handover and component map](DEVELOPER_HANDOVER.md)
- [Backend architecture](architecture/backend.md)
- [Frontend architecture](architecture/frontend.md)
- [Database architecture](architecture/database.md)

### Features and API

- [API overview](api/overview.md)
- [Booking flow](features/booking-flow.md)
- [Time slots](features/time-slots.md)
- [OTP verification](features/otp-verification.md)
- [Razorpay route](razorpay-route.md)

### Setup and operations

- [Local setup](../SETUP.md)
- [Docker deployment](deployment/docker.md)
- [Production deployment](deployment/production.md)
- [Environment variables](reference/environment-vars.md)
- [Troubleshooting](reference/troubleshooting.md)
- [Super Admin user handbook](reference/superadmin-user-handbook.md)

Some architecture, API, and deployment pages predate the current multi-service Compose stack. Treat code, migrations, Compose wiring, and the running OpenAPI schema as authoritative when details conflict, and update the relevant document as part of future changes.
