# Aurexa WhatsApp gateway — Baileys sidecar (one socket per organization).
FROM node:20-slim AS deps
WORKDIR /srv
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

FROM node:20-slim
WORKDIR /srv
ENV NODE_ENV=production
COPY --from=deps /srv/node_modules ./node_modules
COPY package.json src ./src/
# Auth state volume mount point (per-connection dirs, 0700 at runtime).
RUN mkdir -p /data/wa-auth && chmod 700 /data/wa-auth
EXPOSE 3100
CMD ["node", "src/index.js"]
