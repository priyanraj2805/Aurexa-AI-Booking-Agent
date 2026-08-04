# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# ── Runtime configuration ─────────────────────────────────────────────────────
EXPOSE 8006

RUN sed -i 's/\r$//' docker-entrypoint.sh && \
    chmod +x docker-entrypoint.sh

CMD ["./docker-entrypoint.sh"]
