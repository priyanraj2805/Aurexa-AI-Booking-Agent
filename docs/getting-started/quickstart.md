# Quick Start Guide

> Get up and running in 5 minutes

This guide covers the minimum steps to run the complete system locally. For detailed installation, see [Installation Guide](installation.md).

---

## Prerequisites

Ensure you have:

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.11+ | Backend runtime |
| Node.js | 18+ | Frontend runtime |
| PostgreSQL | 14+ | Database |

---

## Step 1: Clone & Configure

```bash
# Navigate to backend
cd backend

# Copy environment template
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Database
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_password
POSTGRES_SERVER=localhost
POSTGRES_PORT=5432
POSTGRES_DB=booking_agent

# Auth
JWT_SECRET_KEY=your_secure_random_key

# LLM (Groq)
LLM_API_KEY=your_groq_api_key

# Email (optional, for OTP)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=your_app_password
```

Generate a secure JWT key:
```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

---

## Step 2: Start Database

Ensure PostgreSQL is running:

```bash
# macOS (Homebrew)
brew services start postgresql@14

# Windows
# Start PostgreSQL service from Services app

# Linux
sudo systemctl start postgresql
```

Create the database:
```bash
createdb booking_agent
```

Run migrations:
```bash
cd backend
alembic upgrade head
```

---

## Step 3: Start Backend

```bash
# Terminal 1
cd backend
python run.py
```

Backend runs at: **http://localhost:8000**

Test it's working:
```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

---

## Step 4: Start Frontend

```bash
# Terminal 2 - Admin Panel
cd frontend/admin-panel
npm install
npm run dev
```

```bash
# Terminal 3 - Chat Widget
cd frontend/chat-widget
npm install
npm run dev
```

---

## Step 5: Use the Application

### Admin Panel (for businesses)
- **URL:** http://localhost:3002
- **First Time:** Create account → Create organization → Get API key

### Chat Widget (for customers)
- **URL:** http://localhost:5174
- **Embed Code:** Get from Admin Panel → Integrations page

---

## What's Next?

| Goal | Next Step |
|------|-----------|
| Understand the system | [Architecture Overview](../architecture/system-overview.md) |
| Configure features | [Configuration Guide](configuration.md) |
| Deploy to production | [Production Deployment](../deployment/production.md) |
| Explore API | [API Overview](../api/overview.md) |

---

## Common Issues

### "Connection refused" errors
- Ensure PostgreSQL is running
- Check database credentials in `.env`

### "LLM API error"
- Verify `LLM_API_KEY` is correct in `.env`
- Check Groq account has available credits

### "Port already in use"
- Stop other services using ports 8000, 3002, or 5174

---

## Need Help?

- **Troubleshooting:** See [Troubleshooting Guide](../reference/troubleshooting.md)
- **Full Installation:** See [Installation Guide](installation.md)
