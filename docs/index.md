# Documentation Hub

Welcome to the DotStark AI Booking Agent documentation. Here you'll find everything you need to understand, set up, and extend the platform.

---

## 🎯 Find Your Path

### I'm New to This Project

1. **Quick Overview** — Read the [README](../README.md)
2. **5-Minute Setup** — [Getting Started](getting-started/quickstart.md)
3. **Understand the Architecture** — [System Overview](architecture/system-overview.md)

### I'm a Developer

1. **Backend Architecture** — [Backend Deep Dive](architecture/backend.md)
2. **Frontend Architecture** — [Frontend Deep Dive](architecture/frontend.md)
3. **Database Design** — [Database Schema](architecture/database.md)
4. **API Reference** — [API Overview](api/overview.md)

### I'm Deploying to Production

1. **Production Deployment** — [Deployment Guide](deployment/production.md)
2. **Environment Variables** — [Configuration Reference](reference/environment-vars.md)
3. **Troubleshooting** — [Common Issues](reference/troubleshooting.md)

---

## 📖 Documentation Index

### Getting Started
| Document | Description | Level |
|----------|-------------|-------|
| [Quick Start](getting-started/quickstart.md) | 5-minute setup guide | ⭐ Beginner |
| [Installation](getting-started/installation.md) | Full installation steps | Beginner |
| [Configuration](getting-started/configuration.md) | Environment setup | Beginner |

### Architecture
| Document | Description | Level |
|----------|-------------|-------|
| [System Overview](architecture/system-overview.md) | High-level architecture | ⭐ Beginner |
| [Backend](architecture/backend.md) | Backend components & logic | Intermediate |
| [Frontend](architecture/frontend.md) | Admin panel & widget | Intermediate |
| [Database](architecture/database.md) | Schema & design patterns | Intermediate |
| [LLM Engine](architecture/llm-engine.md) | AI integration details | Advanced |

### API Reference
| Document | Description | Level |
|----------|-------------|-------|
| [API Overview](api/overview.md) | Authentication & patterns | Intermediate |
| [Endpoints](api/endpoints.md) | All API endpoints | Intermediate |

### Features
| Document | Description | Level |
|----------|-------------|-------|
| [Booking Flow](features/booking-flow.md) | How booking works | ⭐ Beginner |
| [Time Slots](features/time-slots.md) | Availability management | Intermediate |
| [OTP Verification](features/otp-verification.md) | Security flow | Intermediate |

### Deployment
| Document | Description | Level |
|----------|-------------|-------|
| [Production](deployment/production.md) | Production setup | Advanced |
| [Docker](deployment/docker.md) | Docker deployment | Advanced |

### Reference
| Document | Description | Level |
|----------|-------------|-------|
| [Environment Variables](reference/environment-vars.md) | All config options | Reference |
| [Troubleshooting](reference/troubleshooting.md) | Common issues | Reference |
| [FAQ](reference/faq.md) | Frequently asked | Reference |

---

## 🔑 Key Concepts

### "Dumb LLM, Smart Python"
The core architectural principle: LLM only handles natural language understanding (intent + entities), while all business logic runs in deterministic Python code.

### Multi-Tenancy
Single deployment serves multiple organizations with complete data isolation via `org_id` scoping.

### State Management
Chat sessions stored in PostgreSQL as JSONB, allowing flexible field collection without schema changes.

---

## Contributing

Found an error or want to improve these docs? See [CONTRIBUTING.md](../CONTRIBUTING.md).

---

*Last updated: March 2026*
