# Booking Agent Deployment Package

This folder contains all the source files needed to run the Booking Agent project on a new device.

## What's Included

### Backend (`/backend`)
- FastAPI application source code (`app/`)
- Database migrations (`alembic/`)
- Tests (`tests/`)
- Docker configuration (Dockerfile, docker-compose.yml)
- Python dependencies (requirements.txt)

### Frontend (`/frontend`)
- Admin Panel (`admin-panel/`) - Next.js dashboard
- Chat Widget (`chat-widget/`) - Embeddable booking widget

### Configuration Files
- `docker-compose.yml` - Orchestrates all services
- `.env.example` & `backend/.env.example` - Environment variable templates
- `SETUP.md` - Detailed setup instructions
- `start.ps1` / `start.bat` - Startup scripts

---

## Quick Start

### Prerequisites
1. **Docker** - Install Docker Desktop or Docker Engine
2. **A terminal** - PowerShell (Windows) or bash (Mac/Linux)

### Setup Steps

1. **Navigate to the deployment folder:**
   ```powershell
   cd path\to\deployment
   ```

2. **Copy environment files:**
   ```powershell
   # Windows
   Copy-Item .env.example -Destination .env
   Copy-Item backend\.env.example -Destination backend\.env.docker

   # Mac/Linux
   cp .env.example .env
   cp backend/.env.example backend/.env.docker
   ```

3. **Configure environment variables:**
   Open `backend/.env.docker` in a text editor and fill in:
   - `LLM_API_KEY` - Your Groq API key
   - `SMTP_USER` / `SMTP_PASSWORD` - Email credentials
   - `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` - Google OAuth (optional)
   - `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` - Microsoft OAuth (optional)

4. **Start the project:**
   ```powershell
   # Windows (recommended)
   .\start.ps1

   # Or directly
   docker compose up --build
   ```

---

## Service URLs

After starting, you'll see these URLs in the terminal:

| Service | Local URL | Public URL (Cloudflare) |
|--------|----------|---------------------|
| Admin Panel | http://localhost:3002 | https://*.trycloudflare.com |
| Backend API | http://localhost:8005 | https://*.trycloudflare.com |
| API Docs | http://localhost:8005/docs | - |

---

## Project Structure

```
deployment/
├── backend/
│   ├── app/              # FastAPI application
│   │   ├── api/          # API endpoints
│   │   ├── core/         # Core utilities
│   │   ├── crud/         # Database operations
│   │   ├── db/           # Database config
│   │   ├── engine/       # Booking engine
│   │   ├── models/       # SQLAlchemy models
│   │   └── schemas/      # Pydantic schemas
│   ├── alembic/          # Database migrations
│   ├── tests/           # Test suite
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── requirements.txt
├── frontend/
│   ├── admin-panel/     # Next.js dashboard
│   └── chat-widget/     # Embeddable widget
├── docs/                # Documentation
├── docker-compose.yml  # Root compose file
├── .env.example
├── SETUP.md
└── start.ps1
```

---

## Need Help?

See `SETUP.md` for detailed instructions, or contact the developer who provided this deployment package.