# Docker Deployment

> Deploy DotStark using Docker containers

This document covers containerizing and deploying the application using Docker.

---

## 1. Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         DOCKER ARCHITECTURE                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                     Docker Compose                                │   │
│  │                                                                   │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │   │
│  │  │   backend   │  │  postgres   │  │    nginx    │            │   │
│  │  │  (FastAPI)  │  │  (Database) │  │  (Reverse   │            │   │
│  │  │   :8000     │  │   :5432     │  │   Proxy)    │            │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘            │   │
│  │                                                                   │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
│  Volumes:                                                                │
│  - postgres_data (database files)                                        │
│  - ./backend:/app (code)                                                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Dockerfiles

### Backend Dockerfile

```dockerfile
# backend/Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Create requirements.txt if needed

```bash
cd backend
pip freeze > requirements.txt
```

---

## 3. Docker Compose

### docker-compose.yml

```yaml
version: '3.8'

services:
  backend:
    build: ./backend
    ports:
      - "8000:8000"
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_SERVER=postgres
      - POSTGRES_PORT=5432
      - POSTGRES_DB=booking_agent
      - JWT_SECRET_KEY=${JWT_SECRET_KEY}
      - LLM_API_KEY=${LLM_API_KEY}
      - SMTP_HOST=${SMTP_HOST}
      - SMTP_PORT=${SMTP_PORT}
      - SMTP_USER=${SMTP_USER}
      - SMTP_PASSWORD=${SMTP_PASSWORD}
      - EMAILS_FROM_EMAIL=${EMAILS_FROM_EMAIL}
    depends_on:
      - postgres
    restart: unless-stopped

  postgres:
    image: postgres:14
    environment:
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=postgres
      - POSTGRES_DB=booking_agent
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped

  nginx:
    image: nginx:latest
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
    depends_on:
      - backend
    restart: unless-stopped

volumes:
  postgres_data:
```

---

## 4. Nginx Configuration

### nginx.conf

```nginx
events {
    worker_connections 1024;
}

http {
    upstream backend {
        server backend:8000;
    }

    server {
        listen 80;
        server_name localhost;

        location / {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        location /health {
            proxy_pass http://backend/health;
            access_log off;
        }
    }
}
```

---

## 5. Environment File

### .env

```bash
# Generate JWT secret
JWT_SECRET_KEY=your-64-character-random-string-here

# Groq API
LLM_API_KEY=gsk_xxxxxxxxxxxxx

# SMTP
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
EMAILS_FROM_EMAIL=your-email@gmail.com
```

---

## 6. Building & Running

### Build Images

```bash
# Build all services
docker-compose build
```

### Start Services

```bash
# Start in detached mode
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Verify

```bash
# Check backend health
curl http://localhost:8000/health

# Check containers
docker-compose ps
```

---

## 7. Database Migrations

### Run Migrations

```bash
# Run migrations in the backend container
docker-compose exec backend alembic upgrade head
```

### Create Migration

```bash
docker-compose exec backend alembic revision --autogenerate -m "description"
```

---

## 8. Production Considerations

### Using Named Volumes

```yaml
volumes:
  postgres_data:
    driver: local
```

### Health Checks

```yaml
services:
  backend:
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

### Resource Limits

```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
```

---

## 9. Frontend Deployment

### Build Admin Panel

```bash
cd frontend/admin-panel
docker build -t dotstark-admin-panel .
```

### Serve Widget Statically

```bash
# Build widget
cd frontend/chat-widget
npm run build

# Serve from nginx
```

---

## 10. Docker Swarm / Kubernetes

For scaling to multiple nodes:

### Docker Swarm

```bash
# Initialize swarm
docker swarm init

# Deploy stack
docker stack deploy -c docker-compose.yml dotstark
```

### Recommended for Production

| Tool | Use Case |
|------|----------|
| Docker Swarm | Simple cluster |
| Kubernetes | Large scale |
| AWS ECS | AWS integration |
| Render/Railway | Managed containers |

---

## 11. Related Documents

| Document | What It Covers |
|----------|----------------|
| [Production Deployment](production.md) | VM-based deployment |
| [Environment Variables](reference/environment-vars.md) | Configuration |
| [Troubleshooting](reference/troubleshooting.md) | Common issues |

---

*Level: Intermediate — Familiarity with Docker and containers recommended.*
