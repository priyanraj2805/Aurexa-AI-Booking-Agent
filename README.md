# 🤖 DotStark AI Booking Agent

**Enterprise-grade conversational AI booking platform. Enable your customers to book appointments via natural conversation.**

<p align="center">
  <a href="https://fastapi.tiangolo.com"><img src="https://img.shields.io/badge/FastAPI-005571?style=flat&logo=fastapi" alt="FastAPI"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/Python-3.11+-3776AB?style=flat&logo=python" alt="Python"></a>
  <a href="https://nextjs.org"><img src="https://img.shields.io/badge/Next.js-000000?style=flat&logo=next.js" alt="Next.js"></a>
  <a href="https://www.postgresql.org"><img src="https://img.shields.io/badge/PostgreSQL-336791?style=flat&logo=postgresql" alt="PostgreSQL"></a>
</p>

## 📋 Table of Contents
- [Quick Start](#-quick-start)
- [What Is This?](#-what-is-this)
- [Architecture](#-architecture)
- [Project Structure](#-project-structure)
- [Tech Stack](#-technology-stack)
- [Security](#-security-features)
- [Documentation](#-documentation)
- [Getting Help](#-need-help)

---

## 🎯 What Is This?

Turn your website visitors into booked customers with an AI assistant that handles appointment scheduling through natural conversation.

### Why Use This?

✅ **24/7 Booking** - AI handles appointments even when you're asleep  
✅ **Natural Conversations** - Customers book by typing normally ("I need a haircut tomorrow at 3pm")  
✅ **Zero Learning Curve** - Embed on any website in minutes  
✅ **Serve 100+ Businesses** - Single deployment for multiple organizations  

### Key Capabilities

| Feature | What It Does |
|---------|--------------|
| 🤖 **AI Chat Widget** | Embed on any website, customers chat naturally |
| 📊 **Admin Dashboard** | Manage bookings, availability, and settings |
| 📅 **Smart Scheduling** | Real-time availability, prevents double-booking |
| 🔒 **OTP Verification** | Secure bookings with email confirmation |
| 🏢 **Multi-Tenant** | One system serves multiple businesses securely |

---

## ⚡ Quick Start (5 Minutes)

### Step 1: Setup & Configure

```bash
git clone <repository-url>
cd booking_agent

cd backend
cp .env.example .env
# Open .env and add your API keys
```

### Step 2: Start in 3 Terminals

**Terminal 1** - Backend Server:
```bash
cd booking_agent/backend
python run.py
```

**Terminal 2** - Admin Dashboard:
```bash
cd booking_agent/frontend/admin-panel
npm run dev
```

**Terminal 3** - Chat Widget:
```bash
cd booking_agent/frontend/chat-widget
npm run dev
```

### Step 3: Open Your Apps

- 🎛️ **Admin Dashboard:** http://localhost:3002
- 💬 **Chat Widget Demo:** http://localhost:5174

**That's it!** Now embed the widget on any website.

---

## 🏗️ Architecture

### High-Level Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Your Website                             │
│  ┌─────────────────────┐     ┌──────────────────────────┐  │
│  │   Chat Widget       │     │   Admin Panel            │  │
│  │   (Web Component)  │     │   (Next.js Dashboard)    │  │
│  │   :5174            │     │   :3002                  │  │
│  └──────────┬──────────┘     └────────────┬─────────────┘  │
│             │                             │                 │
│             └──────────┬──────────────────┘                 │
│                        ▼                                    │
│              ┌─────────────────────┐                        │
│              │    FastAPI Backend   │                        │
│              │    (Port 8000)       │                        │
│              └──────────┬──────────┘                        │
│                         │                                    │
│    ┌────────────────────┼────────────────────┐             │
│    ▼                    ▼                    ▼             │
│ ┌────────┐       ┌────────────┐       ┌──────────┐        │
│ │   LLM  │       │ PostgreSQL │       │   SMTP   │        │
│ │ (Groq) │       │ (State/DB) │       │ (Email)  │        │
│ └────────┘       └────────────┘       └──────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### Core Design Principle: "Dumb LLM, Smart Python"

The LLM does **only two things**:
1. **Intent Classification** — Recognizes what user wants (e.g., "book appointment" → `START_BOOKING`)
2. **Entity Extraction** — Pulls data from message (e.g., "I'm John, want 3pm tomorrow" → `{name: "John", date: "2026-03-18", time: "3:00 PM"}`)

**All business logic** — validation, state management, booking transactions — runs in deterministic Python code. This eliminates hallucination risk while keeping conversations natural.

---

## 📁 Project Structure

```
booking_agent/
├── backend/                    # FastAPI application
│   ├── app/
│   │   ├── api/v1/endpoints/  # API route handlers
│   │   ├── core/             # Config, security, mail
│   │   ├── crud/             # Database operations
│   │   ├── engine/           # LLM orchestration & logic
│   │   ├── models/           # SQLAlchemy models
│   │   └── schemas/          # Pydantic schemas
│   ├── alembic/              # Database migrations
│   └── requirements.txt
│
├── frontend/
│   ├── admin-panel/          # Next.js admin dashboard
│   └── chat-widget/          # Vanilla JS web component
│
└── docs/                     # Detailed documentation
```

---

## 🔧 Technology Stack

| Layer | Technology |
|-------|-----------|
| Backend Framework | Python 3.11+, FastAPI |
| Database | PostgreSQL + SQLAlchemy 2.0 (async) |
| ORM | SQLAlchemy with asyncpg |
| LLM | Groq API (moonshotai/kimi-k2-instruct) |
| Admin Panel | Next.js 14 + shadcn/ui |
| Chat Widget | Vanilla JavaScript (Web Component) |
| Authentication | JWT (HS256) + API Keys |
| Email | SMTP (Gmail) |

---

## 🔐 Security Features

- **Multi-Tenant Isolation** — Data scoped by `org_id` at database level
- **API Key Auth** — Widget requests use `X-Org-API-Key` header
- **JWT Auth** — Admin panel uses Bearer tokens (7-day expiry)
- **Rate Limiting** — slowapi prevents abuse
- **OTP Protection** — Max 5 attempts, brute-force blocked
- **Row-Level Locking** — Concurrent booking prevention with `SELECT FOR UPDATE`
- **Input Validation** — All user inputs sanitized and length-limited
- **XSS Prevention** — Safe DOM manipulation in widget

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- PostgreSQL 14+

### Detailed Setup

See [Quick Start Guide](docs/getting-started/quickstart.md) for complete installation steps.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `POSTGRES_*` | Database connection |
| `JWT_SECRET_KEY` | Auth token signing |
| `LLM_API_KEY` | Groq API key |
| `SMTP_*` | Email configuration |

---

## 📚 Documentation

### For Different Users

| Role | Start Here |
|------|------------|
| **Business User** | Admin Panel — http://localhost:3002 |
| **End Customer** | Chat Widget — embedded on website |
| **New Developer** | [Quick Start](docs/getting-started/quickstart.md) |
| **Contributor** | [Contributing Guide](CONTRIBUTING.md) |

### Documentation Structure

```
docs/
├── getting-started/
│   ├── quickstart.md      # 5-minute setup
│   └── installation.md    # Full installation guide
├── architecture/
│   ├── system-overview.md # High-level design
│   ├── backend.md         # Backend components
│   └── frontend.md        # Frontend components
├── api/
│   └── overview.md        # API documentation
└── features/
    └── booking-flow.md    # How booking works
```

---

## 🤝 Contributing

Contributions welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## 📄 License

Proprietary — DotStark Technologies

---

## Need Help?

- **Issues:** Open a GitHub issue
- **Documentation:** See `/docs` folder for detailed guides
