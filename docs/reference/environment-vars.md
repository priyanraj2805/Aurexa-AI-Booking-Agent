# Environment Variables

> Complete reference for all configuration options

This document lists all environment variables used by the application.

---

## 1. Database Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_USER` | Yes | `postgres` | Database username |
| `POSTGRES_PASSWORD` | Yes | — | Database password |
| `POSTGRES_SERVER` | Yes | `localhost` | Database host |
| `POSTGRES_PORT` | Yes | `5432` | Database port |
| `POSTGRES_DB` | Yes | `booking_agent` | Database name |

**Connection String Format:**
```
postgresql+asyncpg://user:password@host:port/dbname
```

---

## 2. Authentication & Security

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `JWT_SECRET_KEY` | Yes | — | Secret key for JWT signing (64+ chars) |
| `JWT_ALGORITHM` | No | `HS256` | JWT algorithm |
| `JWT_EXPIRATION_MINUTES` | No | `10080` | Token expiry (7 days) |
| `DEBUG` | No | `false` | Enable debug mode |

**Generate JWT Secret:**
```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

---

## 3. LLM Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_API_KEY` | Yes | — | Groq API key |
| `LLM_BASE_URL` | No | Groq URL | API endpoint |
| `LLM_MODEL` | No | See below | Model to use |

**Default Model:**
```
moonshotai/kimi-k2-instruct-0905
```

---

## 4. Email (SMTP)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SMTP_HOST` | No | — | SMTP server hostname |
| `SMTP_PORT` | No | `587` | SMTP port |
| `SMTP_USER` | No | — | SMTP username |
| `SMTP_PASSWORD` | No | — | SMTP password |
| `EMAILS_FROM_EMAIL` | No | — | Sender email address |
| `EMAILS_FROM_NAME` | No | `DotStark` | Sender name |

---

## 5. Application Settings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PROJECT_NAME` | No | DotStark AI Booking | Project name |
| `API_V1_STR` | No | `/api/v1` | API prefix |

---

## 6. Example .env File

```bash
# Database
POSTGRES_USER=postgres
POSTGRES_PASSWORD=my_secure_password
POSTGRES_SERVER=localhost
POSTGRES_PORT=5432
POSTGRES_DB=booking_agent

# Security
JWT_SECRET_KEY=aVeryLongRandomStringGeneratedByPythonSecretsModule
DEBUG=false

# LLM
LLM_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.groq.com/openai/v1/chat/completions
LLM_MODEL=moonshotai/kimi-k2-instruct-0905

# Email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAILS_FROM_EMAIL=bookings@yourdomain.com

# App
PROJECT_NAME=DotStark AI Booking
API_V1_STR=/api/v1
```

---

## 7. Development vs Production

### Development (.env)

```bash
DEBUG=true
POSTGRES_SERVER=localhost
JWT_SECRET_KEY=dev-secret-key
```

### Production (.env)

```bash
DEBUG=false
POSTGRES_SERVER=prod-db.xxxx.us-east-1.rds.amazonaws.com
JWT_SECRET_KEY=<64-char-random-string-from-python-secrets>
```

---

## 8. Required Variables Summary

| Environment | Required Variables |
|-------------|-------------------|
| Development | `POSTGRES_*`, `JWT_SECRET_KEY`, `LLM_API_KEY` |
| Production | All variables |

---

## 9. Related Documents

| Document | What It Covers |
|----------|----------------|
| [Quick Start](../getting-started/quickstart.md) | Basic setup |
| [Production Deployment](../deployment/production.md) | Production config |
| [Troubleshooting](troubleshooting.md) | Common issues |

---

*Reference document — No need to read sequentially.*
