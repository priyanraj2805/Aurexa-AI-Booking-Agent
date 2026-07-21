# Production Deployment

> Guide for deploying DotStark Booking Agent to production

This document covers the steps required to deploy the platform to a production environment.

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         PRODUCTION ARCHITECTURE                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐           │
│  │   CDN/Load   │     │   CDN/Load   │     │   CDN/Load   │           │
│  │   Balancer   │     │   Balancer   │     │   Balancer   │           │
│  └──────┬───────┘     └──────┬───────┘     └──────┬───────┘           │
│         │                    │                    │                     │
│         ▼                    ▼                    ▼                     │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐           │
│  │  Admin UI   │     │  Chat Widget │     │  Backend    │           │
│  │  (Next.js) │     │  (Static JS) │     │  (FastAPI)  │           │
│  └──────────────┘     └──────────────┘     └──────┬───────┘           │
│                                                     │                    │
│                              ┌──────────────────────┼──────────┐        │
│                              ▼                      ▼          ▼        │
│                       ┌─────────────┐      ┌───────────┐ ┌─────────┐   │
│                       │ PostgreSQL  │      │   Groq   │ │  SMTP  │   │
│                       │  (RDS)     │      │   API    │ │ (Email)│   │
│                       └─────────────┘      └───────────┘ └─────────┘   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Prerequisites

### Infrastructure Requirements

| Component | Requirement |
|-----------|-------------|
| Backend Server | 2+ vCPU, 4GB RAM |
| Database | PostgreSQL 14+ (managed recommended) |
| CDN | CloudFlare, CloudFront, or similar |
| Domain | Registered with SSL |

### Required Services

| Service | Purpose | Recommended |
|---------|---------|-------------|
| Database | PostgreSQL | AWS RDS, Cloud SQL |
| LLM API | Groq | groq.com |
| Email | SMTP | SendGrid, AWS SES, Postmark |

---

## 3. Environment Configuration

### Production .env

```bash
# Backend/.env

# Database (PostgreSQL)
POSTGRES_USER=produser
POSTGRES_PASSWORD=<strong-random-password>
POSTGRES_SERVER=my-project.us-east-1.rds.amazonaws.com
POSTGRES_PORT=5432
POSTGRES_DB=booking_agent

# JWT (generate with: python -c "import secrets; print(secrets.token_urlsafe(64))")
JWT_SECRET_KEY=<64-character-random-string>

# LLM
LLM_API_KEY=gsk_xxxxxxxxxxxxxxxx
LLM_BASE_URL=https://api.groq.com/openai/v1/chat/completions
LLM_MODEL=moonshotai/kimi-k2-instruct-0905

# Email (SMTP)
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=<sendgrid-api-key>
EMAILS_FROM_EMAIL=bookings@yourdomain.com

# Security
DEBUG=false
```

---

## 4. Backend Deployment

### Option A: Virtual Machine (EC2, etc.)

```bash
# 1. Setup server
ssh ubuntu@your-server
sudo apt update && sudo apt install -y python3.11 python3-pip nginx

# 2. Create service user
sudo useradd -r -s /bin/false dotstark

# 3. Deploy code
sudo mkdir -p /var/www/dotstark
sudo git clone <repo> /var/www/dotstark
cd /var/www/dotstark/backend

# 4. Setup virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 5. Setup systemd service
sudo cat > /etc/systemd/system/dotstark.service << EOF
[Unit]
Description=DotStark Booking API
After=network.target

[Service]
User=dotstark
Group=dotstark
WorkingDirectory=/var/www/dotstark/backend
Environment="PATH=/var/www/dotstark/backend/venv/bin"
ExecStart=/var/www/dotstark/backend/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
EOF

# 6. Start service
sudo systemctl daemon-reload
sudo systemctl enable dotstark
sudo systemctl start dotstark
```

### Option B: Container (Docker)

See [Docker Deployment](docker.md) for detailed container setup.

---

## 5. Frontend Deployment

### Admin Panel (Next.js)

```bash
cd frontend/admin-panel

# Build for production
npm run build

# Output in .next/
# Deploy to:
# - Vercel (recommended)
# - AWS Amplify
# - Self-hosted with nginx
```

### Chat Widget

```bash
cd frontend/chat-widget
npm run build

# Output: dist/widget.js
# Host on CDN or alongside backend static/
```

---

## 6. Nginx Configuration

### Backend Proxy

```nginx
# /etc/nginx/sites-available/api.dotstark.com
server {
    listen 443 ssl http2;
    server_name api.dotstark.com;

    ssl_certificate /etc/letsencrypt/live/api.dotstark.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.dotstark.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Admin Panel

```nginx
# /etc/nginx/sites-available/admin.dotstark.com
server {
    listen 443 ssl http2;
    server_name admin.dotstark.com;

    ssl_certificate /etc/letsencrypt/live/admin.dotstark.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/admin.dotstark.com/privkey.pem;

    root /var/www/dotstark/admin-panel/.next;
    index index.html;

    location / {
        try_files $next$uri $next$uri/ /index.html;
    }
}
```

---

## 7. SSL/TLS Setup

### Using Let's Encrypt

```bash
# Install certbot
sudo apt install certbot python3-certbot-nginx

# Get certificate
sudo certbot --nginx -d api.dotstark.com
sudo certbot --nginx -d admin.dotstark.com
sudo certbot --nginx -d widget.dotstark.com

# Auto-renewal
sudo certbot renew --dry-run
```

---

## 8. Database Setup

### Managed PostgreSQL (AWS RDS)

1. Create RDS instance (PostgreSQL 14+)
2. Configure security group for backend access
3. Run migrations:

```bash
cd backend
alembic upgrade head
```

---

## 9. Monitoring & Logging

### Recommended Stack

| Tool | Purpose |
|-------|---------|
| CloudWatch/Papertrail | Log aggregation |
| Sentry | Error tracking |
| Prometheus + Grafana | Metrics |
| Health checks | `/health` endpoint |

### Health Check

```bash
curl https://api.dotstark.com/health
# {"status":"ok"}
```

---

## 10. Security Checklist

- [ ] Use strong `JWT_SECRET_KEY` (64+ random characters)
- [ ] Enable HTTPS everywhere
- [ ] Configure CORS properly
- [ ] Set up database backups
- [ ] Enable rate limiting
- [ ] Configure firewall rules
- [ ] Use managed database with encryption
- [ ] Enable email delivery tracking

---

## 11. Related Documents

| Document | What It Covers |
|----------|----------------|
| [Docker Deployment](docker.md) | Container deployment |
| [Environment Variables](reference/environment-vars.md) | All config options |
| [Troubleshooting](reference/troubleshooting.md) | Common issues |

---

*Level: Advanced — System administration and DevOps experience recommended.*
