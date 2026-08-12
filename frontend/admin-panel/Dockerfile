# ── Stage 1: Dependencies ─────────────────────────────────────────────────────
# Install node modules only — cached if package.json hasn't changed
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ── Stage 2: Builder ──────────────────────────────────────────────────────────
# Build the Next.js production bundle
FROM node:20-alpine AS builder
WORKDIR /app

# Copy node_modules from deps stage
COPY --from=deps /app/node_modules ./node_modules
# Copy all source files
COPY . .

# Build the production app.
# NEXT_TELEMETRY_DISABLED=1 opts out of Next.js anonymous usage stats.
ENV NEXT_TELEMETRY_DISABLED=1
ENV NODE_ENV=production
# Override the dev-time .env.local (localhost) so browser-side API calls go
# through the same public host (proxied by next.config rewrites to the
# internal backend). Keeps tunnels working from any machine, URL-independent.
ENV NEXT_PUBLIC_API_BASE=/api/v1
RUN npm run build

# ── Stage 3: Runner ───────────────────────────────────────────────────────────
# This is the final image — only contains what's needed to RUN the app.
# Not the source code, not node_modules. Much smaller.
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Copy the standalone server output (requires output: 'standalone' in next.config.ts)
COPY --from=builder /app/.next/standalone ./
# Copy static assets (CSS, JS chunks, images)
COPY --from=builder /app/.next/static ./.next/static
# Copy public folder (favicon, logos, etc.)
COPY --from=builder /app/public ./public

EXPOSE 3002

# Next.js standalone server listens on PORT env variable
ENV PORT=3002
ENV HOSTNAME=0.0.0.0

CMD ["node", "server.js"]
