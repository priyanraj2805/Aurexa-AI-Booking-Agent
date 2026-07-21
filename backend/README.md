# DotStark AI Booking Assistant - Backend

A production-ready multi-tenant conversational booking system with AI-powered chat, WhatsApp integration, calendar sync, and video meeting support.

---

## 🚀 Quick Start (5 Minutes)

### Step 1: Setup Environment

```bash
# Navigate to backend folder
cd backend

# Copy environment template
copy .env.example .env
```

### Step 2: Configure Variables

Edit `.env` and fill in required values. See [backend_docs/ENV_SETUP.md](backend_docs/ENV_SETUP.md) for detailed instructions.

**Two options:**

**Option A:** Copy the environment template and fill in values:
```bash
copy .env.example .env
```

**Option B:** If you have an existing `.env` file (from `backend_docs/`), copy it to `backend/.env`

**Minimum Required:**
- `POSTGRES_*` - Database credentials
- `JWT_SECRET_KEY` - Generate with: `python -c "import secrets; print(secrets.token_hex(64))"`
- `LLM_*` - LLM API credentials (Groq recommended - free tier)
- `SMTP_*` - Email for OTP/confirmations

### Step 3: Start Backend

```bash
# Install dependencies
pip install -r requirements.txt

# Run (auto-initializes database & creates default organization)
python run.py
```

### Step 4: Access API

- **Swagger UI:** http://localhost:8005/docs
- **API Base:** http://localhost:8005/api/v1

### Default Login Credentials

The backend comes pre-configured with a default organization and user:

| Field | Value |
|-------|-------|
| Email | devang.dotstark@outlook.com |
| Password | Dotstark@2026 |
| Organization | DotStark Technologies |
| API Key | ds_yp7GG8AOp0Qtxzue4QDEu7We9LZohfMs |
| Widget Token | ds_wid_uDy9_VGnwG5YAr3-WUKNH_CKXROWh43j |

> **Note:** Change the password in production!

---

## 📋 What's Included

### Auto-Initialization

On first run, the backend automatically:
- ✅ Runs all database migrations
- ✅ Creates default organization "DotStark Technologies"
- ✅ Sets up business hours and meeting policies

### Features Ready to Use

| Feature | Status |
|---------|--------|
| AI Chat Booking | ✅ Ready |
| Web Widget API | ✅ Ready |
| OTP Verification | ✅ Ready |
| Email Notifications | ✅ Ready |
| Google Calendar | 🔧 Configure to enable |
| Microsoft Outlook | 🔧 Configure to enable |
| WhatsApp | 🔧 Configure to enable |
| Video Meetings (Teams/Meet/Zoom) | 🔧 Configure to enable |

---

## 📁 Project Structure

```
backend/
├── app/                      # Main application code
│   ├── api/v1/              # API endpoints
│   ├── core/                # Core functionality
│   ├── crud/                # Database operations
│   ├── db/                  # Database config
│   ├── engine/              # Business logic
│   ├── models/              # SQLAlchemy models
│   └── schemas/             # Pydantic schemas
├── alembic/                 # Database migrations
├── static/widget/           # Web widget files
├── backend_docs/            # Documentation
├── run.py                   # Main entry point
├── init_db.py               # Database initialization
├── requirements.txt         # Python dependencies
├── .env                     # Your environment variables
├── .env.example             # Environment template
└── docker-compose.yml       # Docker setup
```

---

## 📖 Documentation

### [backend_docs/DEPLOYMENT.md](backend_docs/DEPLOYMENT.md)
Complete deployment guide covering:
- Prerequisites
- Environment setup
- Running the backend
- Docker deployment
- Production considerations
- Troubleshooting

### [backend_docs/ENV_SETUP.md](backend_docs/ENV_SETUP.md)
Step-by-step guide to get every environment variable:
- Database credentials
- LLM API keys (Groq, OpenAI, Anthropic)
- Gmail/Outlook email setup
- Google Calendar OAuth
- Microsoft Outlook OAuth
- WhatsApp Business API
- Video meeting providers

### [backend_docs/API_DOCUMENTATION.md](backend_docs/API_DOCUMENTATION.md)
Complete API reference:
- All endpoints with examples
- Authentication methods
- Request/response formats
- Widget integration guide

---

## 🔧 Common Setup Tasks

### Want to connect Google Calendar?
→ See [backend_docs/ENV_SETUP.md → Google Calendar](backend_docs/ENV_SETUP.md#google-calendar)

### Want to enable WhatsApp?
→ See [backend_docs/ENV_SETUP.md → WhatsApp](backend_docs/ENV_SETUP.md#whatsapp)

### Want to change default organization settings?
→ After starting backend, use dashboard API or admin panel

### Need to add more integrations?
→ See [backend_docs/DEPLOYMENT.md → Integrations](backend_docs/DEPLOYMENT.md#integrations)

---

## 🐳 Docker Deployment

```yaml
# Example docker-compose.yml
version: '3.8'
services:
  backend:
    build: ./backend
    ports:
      - "8005:8005"
    env_file:
      - ./backend/.env
    depends_on:
      - postgres

  postgres:
    image: postgres:14
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: your_password
      POSTGRES_DB: dotstark_v2
    volumes:
      - postgres_data:/var/lib/postgresql/data
```

```bash
# Build and run
docker-compose up -d
```

---

## 🔐 Security Checklist (Before Production)

- [ ] Change `JWT_SECRET_KEY` to a strong random value
- [ ] Set `DEBUG=false` in .env
- [ ] Use strong PostgreSQL password
- [ ] Configure proper CORS origins in app/main.py
- [ ] Enable SSL/TLS (HTTPS)
- [ ] Keep dependencies updated

---

## 🆘 Troubleshooting

### Database Connection Failed
```bash
# Check PostgreSQL is running
pg_isready -h localhost -p 5432
```

### CORS Errors
Add your domain to CORS settings in `app/main.py`:
```python
allow_origins = ["https://your-domain.com"]
```

### Email Not Sending
- Using Gmail? Must use App Password, not regular password
- Check spam folder

### Port Already in Use
```bash
# Find process using port 8005
netstat -ano | findstr :8005

# Kill it or change port in run.py
```

---

## 📞 Need More Help?

1. **API Reference:** Open http://localhost:8005/docs (when running)
2. **Deployment Details:** See [backend_docs/DEPLOYMENT.md](backend_docs/DEPLOYMENT.md)
3. **Environment Variables:** See [backend_docs/ENV_SETUP.md](backend_docs/ENV_SETUP.md)
4. **API Endpoints:** See [backend_docs/API_DOCUMENTATION.md](backend_docs/API_DOCUMENTATION.md)

---

## License

Proprietary - All rights reserved
