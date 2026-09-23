/* Aurexa WhatsApp gateway (Baileys transport).

Owns one WhatsApp Web socket per organization connection. The FastAPI backend
owns all business state (org ownership, connection metadata, sessions,
bookings); this service only manages sockets, QR pairing material, and
message transport over an internal service-key-authenticated HTTP API.

P1 scope: service skeleton — config, health endpoint, graceful shutdown.
Session management lands in P2–P4 (routes return 501 until then).
*/

const express = require('express');
const os = require('node:os');
const path = require('node:path');
const { createSessionManager } = require('./sessionManager');
const { defaultMakeSocket, defaultUseAuthState } = require('./baileys');
const { sessionRoutes } = require('./routes/sessions');
const { serviceKey } = require('./auth');
const { createOutbox } = require('./outbox');
const { createWatchdog } = require('./watchdog');

const PORT = Number.parseInt(process.env.PORT || '3100', 10);
const AUTH_DIR = process.env.WA_AUTH_DIR || '/data/wa-auth';
const LOG_LEVEL = (process.env.LOG_LEVEL || 'info').toLowerCase();

function log(level, ...args) {
  const order = { error: 0, warn: 1, info: 2, debug: 3 };
  if ((order[level] ?? 2) <= (order[LOG_LEVEL] ?? 2)) {
    console.log(JSON.stringify({ ts: new Date().toISOString(), level, msg: args.map(String).join(' ') }));
  }
}

function createManager(deps) {
  return createSessionManager({
    makeSocket: (deps && deps.makeSocket) || defaultMakeSocket,
    useAuthState: (deps && deps.useAuthState) || defaultUseAuthState,
    authDir: (deps && deps.authDir) || AUTH_DIR,
    // Default wiring is the live backend. Tests inject fakes via deps.
    deliverInbound: (deps && deps.deliverInbound) || deliverToBackend,
    sendReply: deps && deps.sendReply,
    notifyEvent: (deps && deps.notifyEvent) || deliverEventToBackend,
    outbox: (deps && deps.outbox) || null,
    logger: { info: (m) => log('info', m), warn: (m) => log('warn', m), error: (m) => log('error', m) },
  });
}

const BACKEND_BASE_URL = (process.env.BACKEND_BASE_URL || 'http://backend:8005').replace(/\/+$/, '');

// P7 inbound delivery: POST the normalized envelope to the backend, which
// resolves the org, runs the booking engine, and returns the reply to send.
async function deliverToBackend(connectionId, envelope) {
  const key = serviceKey();
  if (!key) throw new Error('WA_GATEWAY_SERVICE_KEY is empty');
  // Bounded: an unresponsive backend must fail loudly (retry/exhaust) rather
  // than hang a deliver promise forever with zero logs.
  const timeoutMs = Number.parseInt(process.env.BACKEND_TIMEOUT_MS || '120000', 10);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`${BACKEND_BASE_URL}/api/v1/internal/whatsapp-qr/inbound`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gateway-Key': key },
      body: JSON.stringify({ connectionId, ...envelope }),
      signal: ctrl.signal,
    });
  } catch (err) {
    if (err && err.name === 'AbortError') {
      const timeoutErr = new Error(`backend inbound timed out after ${timeoutMs}ms`);
      timeoutErr.code = 'BACKEND_TIMEOUT';
      throw timeoutErr;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
  if (res.status === 404) {
    const err = new Error('unknown connection at backend');
    err.code = 'UNKNOWN_CONNECTION';
    err.statusCode = 404;
    throw err;
  }
  if (!res.ok) {
    const err = new Error(`backend inbound failed status=${res.status}`);
    err.statusCode = res.status;
    throw err;
  }
  return res.json();
}

// Lifecycle push: spontaneous logout/connect straight to the backend so the
// DB row and unlink alerts update within seconds (no admin polling needed).
// Best-effort: throws after logging; callers must swallow (fire-and-forget).
async function deliverEventToBackend(connectionId, event) {
  const key = serviceKey();
  if (!key) throw new Error('WA_GATEWAY_SERVICE_KEY is empty');
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15000);
  try {
    const res = await fetch(`${BACKEND_BASE_URL}/api/v1/internal/whatsapp-qr/events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gateway-Key': key },
      body: JSON.stringify({ connectionId, event: event && event.type }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`backend events failed status=${res.status}`);
    return res.json();
  } finally {
    clearTimeout(timer);
  }
}

function createApp(deps) {
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '256kb' }));

  app.get('/health', (_req, res) => {
    res.json({ ok: true, service: 'wa-gateway', time: new Date().toISOString() });
  });

  const outbox = (deps && deps.outbox) || createOutbox({});
  const manager = createManager({ ...(deps || {}), outbox });
  const watchdog = (deps && deps.watchdog) || createWatchdog({ manager });
  app.set('wa.manager', manager);
  app.set('wa.outbox', outbox);
  app.set('wa.watchdog', watchdog);
  app.use('/sessions', sessionRoutes(manager));

  // eslint-disable-next-line no-unused-vars
  app.use((err, _req, res, _next) => {
    log('error', 'unhandled route error', err && err.message ? err.message : err);
    res.status(500).json({ detail: 'Gateway internal error' });
  });

  return app;
}

function managerResume(app) {
  const manager = app.get('wa.manager');
  if (!manager || typeof manager.resume !== 'function') return;
  manager
    .resume()
    .then((ids) => log('info', `resumed ${ids.length} paired session(s)`))
    .catch((err) => log('error', `resume failed: ${err.message}`));
}

async function main() {
  if (!serviceKey()) {
    log('warn', 'WA_GATEWAY_SERVICE_KEY is empty — all service routes will 401');
  }
  log('info', `auth dir=${AUTH_DIR}`);
  const app = createApp();
  const manager = app.get('wa.manager');
  const outbox = app.get('wa.outbox');
  const watchdog = app.get('wa.watchdog');
  if (watchdog) watchdog.start();
  const server = app.listen(PORT, '0.0.0.0', () => {
    log('info', `wa-gateway listening on :${PORT}`);
  });
  managerResume(app);

  const shutdown = (signal) => {
    log('info', `received ${signal}, draining...`);
    (async () => {
      try {
        if (watchdog) watchdog.stop();
        if (outbox) await outbox.shutdown(5000);
        if (manager && typeof manager.closeAll === 'function') await manager.closeAll();
      } catch (err) {
        log('error', `drain failed: ${err.message}`);
      }
    })().catch(() => {});
    server.close(() => {
      log('info', 'http server closed, exiting');
      process.exit(0);
    });
    setTimeout(() => {
      log('error', 'shutdown timed out, forcing exit');
      process.exit(1);
    }, 15000).unref();
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

if (require.main === module) {
  main().catch((err) => {
    console.error(JSON.stringify({ level: 'error', msg: `fatal: ${err && err.stack ? err.stack : err}` }));
    process.exit(1);
  });
}

module.exports = { createApp, createManager, deliverToBackend, deliverEventToBackend };
