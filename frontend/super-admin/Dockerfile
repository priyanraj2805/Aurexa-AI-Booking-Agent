# ── Stage 1: Dependencies ─────────────────────────────────────────────────
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ── Stage 2: Builder ──────────────────────────────────────────────────────
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production
# Override the dev-time .env.local (localhost) so browser-side API calls go
# through the same public host (proxied by next.config rewrites to the
# internal backend-super). Keeps tunnels working from any machine, URL-independent.
ENV NEXT_PUBLIC_API_BASE=/api/v1
RUN npm run build

# ── Stage 3: Runner ───────────────────────────────────────────────────────
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3003
ENV PORT=3003
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
