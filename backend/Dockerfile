# ── Stage 1: Builder ─────────────────────────────────────────────────────────
# We use a slim Python 3.11 image as our base.
# "slim" means it's stripped of extras — smaller and faster to download.
FROM python:3.11-slim AS builder

# Set the working directory inside the container.
# All subsequent commands run from /app.
WORKDIR /app

# Copy ONLY the requirements file first.
# Docker caches this layer — if requirements.txt didn't change,
# it won't re-install packages on the next build. Saves a lot of time.
COPY requirements.txt .

# Install Python dependencies.
# --no-cache-dir keeps the image smaller by not saving the pip download cache.
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the backend code.
COPY . .

# ── Runtime configuration ─────────────────────────────────────────────────────
# Tell uvicorn to bind on all interfaces so Docker can route traffic in.
# Port 8005 matches your existing run.py config.
EXPOSE 8005

# Give execute permission for the entrypoint script
# Handle Windows CRLF -> LF issues just in case
RUN sed -i 's/\r$//' docker-entrypoint.sh && \
    chmod +x docker-entrypoint.sh

# The command that runs when the container starts.
CMD ["./docker-entrypoint.sh"]
