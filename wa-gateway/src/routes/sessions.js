// Internal session routes — every route requires the gateway service key.
// The backend resolves organization ownership; connection ids are opaque
// UUIDs here and are validated before touching anything.
const express = require('express');
const { InvalidConnectionId } = require('../sessionManager');
const { requireServiceKey } = require('../auth');

function sessionRoutes(manager) {
  const router = express.Router();
  router.use(requireServiceKey);

  function invalidId(res) {
    return res.status(400).json({ detail: 'Invalid connection id' });
  }

  // Start (or resume) pairing for a connection. Idempotent.
  router.post('/:id/start', async (req, res) => {
    try {
      const snap = await manager.start(req.params.id);
      return res.status(202).json(snap);
    } catch (err) {
      if (err instanceof InvalidConnectionId) return invalidId(res);
      throw err;
    }
  });

  // Latest fresh QR for the admin UI to render. 404 when none/expired —
  // the frontend keeps polling; Baileys re-emits QR on rotation.
  router.get('/:id/qr', (req, res) => {
    try {
      const qr = manager.freshQr(req.params.id);
      if (!qr) return res.status(404).json({ detail: 'NO_FRESH_QR' });
      return res.json({ connectionId: req.params.id, ...qr });
    } catch (err) {
      if (err instanceof InvalidConnectionId) return invalidId(res);
      throw err;
    }
  });

  router.get('/:id/status', (req, res) => {
    try {
      const snap = manager.status(req.params.id);
      if (!snap) return res.status(404).json({ detail: 'UNKNOWN_SESSION' });
      return res.json(snap);
    } catch (err) {
      if (err instanceof InvalidConnectionId) return invalidId(res);
      throw err;
    }
  });

  // Logout: ends the socket and wipes auth state (needs re-scan after).
  router.delete('/:id', async (req, res) => {
    try {
      const snap = await manager.logout(req.params.id);
      return res.json(snap);
    } catch (err) {
      if (err instanceof InvalidConnectionId) return invalidId(res);
      throw err;
    }
  });

  // Force-restart the socket (watchdog / explicit reconnect). Never wipes auth.
  router.post('/:id/reconnect', async (req, res) => {
    try {
      const snap = await manager.forceReconnect(req.params.id);
      if (!snap) return res.status(404).json({ detail: 'UNKNOWN_SESSION' });
      return res.status(202).json(snap);
    } catch (err) {
      if (err instanceof InvalidConnectionId) return invalidId(res);
      throw err;
    }
  });

  // Admin-initiated push (human handover): plain-text DM to a customer
  // chat on this connection. Body: { to, body }. 404 unknown session,
  // 409 socket offline, 400 bad payload.
  router.post('/:id/send', async (req, res) => {
    try {
      const to = String((req.body && req.body.to) || '').trim();
      const body = String((req.body && req.body.body) || '').trim();
      if (!to || !body) return res.status(400).json({ detail: 'to and body are required' });
      const idemKey = req.body && req.body.idemKey ? String(req.body.idemKey) : undefined;
      const out = await manager.sendTo(req.params.id, to, body, idemKey);
      return res.status(202).json(out);
    } catch (err) {
      if (err instanceof InvalidConnectionId) return invalidId(res);
      if (err && err.message === 'NO_SOCKET') return res.status(409).json({ detail: 'SOCKET_OFFLINE' });
      if (err && (err.message === 'EMPTY_BODY' || err.message === 'EMPTY_RECIPIENT')) {
        return res.status(400).json({ detail: err.message });
      }
      throw err;
    }
  });

  router.get('/', (_req, res) => res.json({ sessions: manager.list() }));

  return router;
}

module.exports = { sessionRoutes };
