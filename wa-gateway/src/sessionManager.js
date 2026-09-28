// Multi-tenant Baileys session manager.
//
// One socket per connection id (backend passes its UUIDs; anything else is
// rejected before touching the filesystem). Auth state lives in
// <authDir>/<connectionId>/ via useMultiFileAuthState. All state transitions
// are explicit: starting -> awaiting_qr -> connected | reconnecting | closed,
// logged_out is terminal until start() is called again.
//
// P2 scope: lifecycle + QR + status + logout. Send queue, rate limiting,
// watchdog and graceful socket shutdown land in P4.
const QRCode = require('qrcode');
const { QR_FRESH_MS } = require('./baileys');
const { ensureAuthDir, wipeConnection, discoverPaired } = require('./authStore');
const { normalizeInbound } = require('./inbound');
const { sendViaHandle, withTimeout } = require('./send');

// Presence stanzas ride the same socket as messages: on a half-open socket
// they hang forever with no error. Bound them well below any turn budget so
// a wedged socket delays a turn by seconds, never swallows it silently.
const PRESENCE_TIMEOUT_MS = 3000;
// Minimum gap between transport-failure self-heals for one connection.
const SELF_HEAL_DEBOUNCE_MS = 30 * 1000;

const DELIVER_ATTEMPTS = 3;
const DELIVER_BACKOFF_MS = [1000, 2000, 4000];

// Typing heartbeat: WhatsApp shows "typing…" only for a few seconds per
// composing stanza, while our engine turn takes 20-90s+. Refresh on a beat
// so the customer sees continuous typing until the reply (or failure).
const TYPING_BEAT_MS = 8000;

  const ID_RE = /^[0-9a-fA-F-]{36}$/;

const RECONNECT_BASE_MS = 2000;
const RECONNECT_MAX_MS = 5 * 60 * 1000;
const WHATSAPP_WINDOW_MS = 24 * 60 * 60 * 1000;

// Exponential backoff with jitter for reconnects (P4 hardening).
function backoffDelayMs(attempt) {
  const capped = Math.min(RECONNECT_BASE_MS * 2 ** Math.max(0, attempt), RECONNECT_MAX_MS);
  return capped + Math.floor(Math.random() * 1000);
}

class InvalidConnectionId extends Error {
  constructor() {
    super('Invalid connection id');
    this.code = 'INVALID_ID';
  }
}

function parsePhoneNumber(jid) {
  if (!jid || typeof jid !== 'string') return null;
  const user = jid.split('@')[0].split(':')[0].replace(/\D/g, '');
  return user.length >= 7 ? user : null;
}

function snapshot(id, rec) {
  return {
    connectionId: id,
    status: rec.status,
    phoneNumber: rec.phoneNumber,
    displayName: rec.displayName,
    lastSeenAt: rec.lastSeenAt,
    reconnectAttempts: rec.reconnectAttempts,
    hasFreshQr: !!(rec.qr && Date.now() < rec.qr.expiresAt),
  };
}

function createSessionManager({ makeSocket, useAuthState, authDir, now, logger, deliverInbound, sendReply, outbox, typingBeatMs, notifyEvent }) {
  const clock = now || (() => Date.now());
  const beatMs = typingBeatMs || TYPING_BEAT_MS;
  const log = logger || { info: () => {}, warn: () => {}, error: () => {} };
  // Lifecycle push to the backend (spontaneous logout/connect only).
  // Best-effort by contract: failures never touch socket handling.
  const notify = notifyEvent || (async () => null);
  const fireEvent = (id, event) => {
    try {
      const p = notify(id, event);
      if (p && typeof p.catch === 'function') p.catch(() => {});
    } catch (_) { /* never break socket handling */ }
  };
  const notifyDelivery = async (id, event) => {
    try {
      await notify(id, event);
    } catch (err) {
      log.warn(`delivery status notify failed: ${err && err.message ? err.message : 'unknown'}`);
    }
  };
  const providerMessageId = (value) => {
    if (!value || typeof value !== 'object') return undefined;
    return value.key && value.key.id ? String(value.key.id) : undefined;
  };
  // P7 inbound pipeline (wired by index.js; tests inject fakes).
  const deliver = deliverInbound || (async () => null);
  // P8 outbound: route through the per-connection outbox when available so
  // sends are paced, retried and deduplicated. Falls back to direct send.
  const replyVia = sendReply || (async (id, chatJid, replyDict, idemKey) => {
    const rec = sessions.get(id);
    if (!rec || !rec.handle || typeof rec.handle.sendRaw !== 'function') {
      throw new Error('NO_SOCKET');
    }
    const task = () => sendViaHandle(rec.handle, chatJid, replyDict);
    if (outbox) {
      const out = await outbox.enqueue(id, idemKey || null, task);
      if (out && out.deduped) {
        log.info(`duplicate reply suppressed`);
        return { deduped: true };
      }
      return out ? out.result : undefined;
    }
    return task();
  });
  const sessions = new Map(); // id -> record
  const starting = new Map(); // id -> in-flight start promise
  const rateBuckets = new Map(); // key: `${id}:${chatJid}` -> [timestamps ms] for per-phone 30/min guardrail
  const RATE_LIMIT_MAX = 30;
  const RATE_LIMIT_WINDOW_MS = 60 * 1000;
  function isRateLimited(id, chatJid) {
    const key = `${id}:${String(chatJid || '')}`;
    const nowMs = clock();
    const arr = rateBuckets.get(key) || [];
    const fresh = arr.filter((t) => t > nowMs - RATE_LIMIT_WINDOW_MS);
    if (fresh.length >= RATE_LIMIT_MAX) {
      rateBuckets.set(key, fresh);
      return true;
    }
    fresh.push(nowMs);
    rateBuckets.set(key, fresh);
    if (rateBuckets.size > 2000) {
      for (const [k, v] of rateBuckets) {
        if (!v.length || v[0] < nowMs - RATE_LIMIT_WINDOW_MS) rateBuckets.delete(k);
      }
    }
    return false;
  }

  async function wipeAuth(id) {
    try {
      await wipeConnection(authDir, id);
    } catch (err) {
      log.warn(`wipe auth failed for session: ${err.message}`);
    }
  }

  function get(id) {
    if (!ID_RE.test(id || '')) throw new InvalidConnectionId();
    return sessions.get(id) || null;
  }

  function touch(id) {
    const rec = sessions.get(id);
    if (rec) rec.lastSeenAt = new Date(clock()).toISOString();
  }

  async function handleInboundMessage(id, raw, source) {
    const rec = sessions.get(id);
    if (!rec || !rec.handle) return { dropped: 'no-session' };
    // Any socket activity (even ignored from-me/no-text) proves transport is alive -> touch for watchdog
    touch(id);
    let parsed;
    try {
      parsed = normalizeInbound(raw);
    } catch (err) {
      log.warn(`inbound normalize failed: ${err.message}`);
      return { dropped: 'normalize-error' };
    }
    if (!parsed || !parsed.envelope) {
      const reason = parsed ? parsed.skip : 'empty';
      // Spell out the self-message case: from-me means WhatsApp servers marked
      // this as sent BY the linked business account (any of its devices).
      // Replying here would make the bot talk to itself, so it is dropped.
      log.info(`inbound ignored (${reason === 'from-me' ? 'from-me: sender IS the linked business account' : reason})`);
      return { dropped: reason };
    }
    // Catch-up/history batches ('append') are only processed when fresh —
    // this recovers messages missed during reconnects without ever replying
    // to ancient history (backend dedup is the second line of defense).
    if (source === 'append') {
      const ageSec = Math.floor(clock() / 1000) - (parsed.envelope.timestamp || 0);
      if (ageSec > 120) {
        log.info(`inbound ignored (stale-append age=${ageSec}s)`);
        return { dropped: 'stale-append' };
      }
      log.info(`inbound accepted fresh catch-up message (age=${ageSec}s)`);
    }
    const { last4, jidKind } = require('./diag');
    const envelope = parsed.envelope;
    if (isRateLimited(id, envelope.chatJid)) {
      log.warn(`inbound rate-limited for ${jidKind(envelope.chatJid)}:${last4(envelope.from)} (30/min)`);
      return { dropped: 'rate-limited' };
    }
    log.info(
      `inbound accepted src=${source || 'notify'} from=${jidKind(envelope.chatJid)}:${last4(envelope.from)} ` +
      `msgid=${String(envelope.messageId).slice(0, 24)} text_len=${envelope.text.length}`
    );
    touch(id);
    // Typing heartbeat for the whole turn: immediate composing plus refresh
    // on a beat until the reply is handed off (or we give up). Best-effort;
    // a stale handle after reconnect just no-ops into the catch.
    let typingTimer = null;
    const sendPresence = async (state) => {
      try {
        const live = sessions.get(id);
        const handle = live && live.handle;
        if (handle && typeof handle.presence === 'function') {
          await withTimeout(handle.presence(envelope.chatJid, state), PRESENCE_TIMEOUT_MS, 'PRESENCE_TIMEOUT');
        }
      } catch (err) {
        // A presence timeout on a "connected" socket is the signature of a
        // half-open connection — say so loudly instead of stalling silently.
        log.warn(`presence ${state} timed out or failed: ${(err && err.code) || (err && err.message) || 'unknown'}`);
      }
    };
    const stopTyping = async () => {
      if (typingTimer) {
        clearInterval(typingTimer);
        typingTimer = null;
      }
      await sendPresence('paused');
    };
    await sendPresence('composing');
    typingTimer = setInterval(() => {
      sendPresence('composing').catch(() => {});
    }, beatMs);
    if (typingTimer.unref) typingTimer.unref();
    // Deliver to the backend with retries; Baileys won't redeliver, so a
    // final failure is logged loudly (message is lost by design, V1 scope).
    let result = null;
    let lastErr = null;
    for (let attempt = 0; attempt < DELIVER_ATTEMPTS; attempt += 1) {
      try {
        // eslint-disable-next-line no-await-in-loop
        result = await deliver(id, envelope);
        lastErr = null;
        break;
      } catch (err) {
        // Backend doesn't know this connection (row deleted?) — retrying is pointless.
        if (err && err.code === 'UNKNOWN_CONNECTION') {
          log.warn(`backend has no such connection; dropping inbound`);
          await stopTyping();
          return { dropped: 'unknown-connection' };
        }
        lastErr = err;
        log.warn(`inbound deliver attempt ${attempt + 1} failed: ${err.message}`);
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => setTimeout(r, DELIVER_BACKOFF_MS[attempt] || 4000));
      }
    }
    if (lastErr) {
      log.error(`inbound deliver exhausted, message lost (msgid logged only)`);
      await stopTyping();
      return { dropped: 'deliver-exhausted' };
    }
    const replyDict = result && result.reply ? result.reply : null;
    log.info(
      `backend replied: ${replyDict ? `type=${replyDict.type || '?'} body_len=${String(replyDict.body || '').length}` : 'no-reply'}`
    );
    // P1-7 24h window guardrail: if customer hasn't written in 24h, free-form outside window should use template
    // For now we log and still send text, but backend can enforce template based on lastSeenAt
    if (replyDict && (replyDict.body || replyDict.type)) {
      const windowStart = rec.lastSeenAt ? Date.parse(rec.lastSeenAt) : 0;
      if (windowStart && clock() - windowStart > WHATSAPP_WINDOW_MS) {
        log.warn(`outside 24h window for ${envelope.chatJid}, free-form reply may need template (24h)`);
      }
      try {
        const sendResult = await replyVia(id, envelope.chatJid, replyDict, `qr-reply:${envelope.messageId}`);
        log.info(`reply handed to outbox for delivery`);
        if (result && result.session_id) {
          await notifyDelivery(id, {
            type: 'message_sent',
            sessionId: result.session_id,
            fromNumber: envelope.from,
            chatJid: envelope.chatJid,
            providerMessageId: providerMessageId(sendResult),
          });
        }
        await stopTyping();
        return { delivered: true, replied: true };
      } catch (err) {
        log.error(`reply send failed: ${err.message}`);
        if (result && result.session_id) {
          await notifyDelivery(id, {
            type: 'message_failed',
            sessionId: result.session_id,
            fromNumber: envelope.from,
            chatJid: envelope.chatJid,
            error: err && err.message ? err.message : 'send failed',
          });
        }
        noteTransportFailure(id, err);
        await stopTyping();
        return { delivered: true, replied: false };
      }
    }
    await stopTyping();
    return { delivered: true, replied: false };
  }

  async function openSocket(id, rec) {
    const dir = await ensureAuthDir(authDir, id);
    const { state, saveCreds } = await useAuthState(dir);
    const handle = await makeSocket({
      authDir: dir,
      state,
      saveCreds,
      log,
      hooks: {
        onMessage: (raw, source) => {
          handleInboundMessage(id, raw, source || 'notify').catch((err) => {
            log.error(`inbound pipeline failed: ${err.message}`);
          });
        },        onQr: async (qrString) => {
          const dataUrl = await QRCode.toDataURL(qrString, { margin: 1, width: 256 });
          const issuedAt = clock();
          rec.qr = { dataUrl, issuedAt, expiresAt: issuedAt + QR_FRESH_MS };
          if (rec.status !== 'connected') rec.status = 'awaiting_qr';
          touch(id);
          log.info(`QR issued for session ${id}`);
        },
        onOpen: (user) => {
          rec.status = 'connected';
          rec.qr = null;
          rec.reconnectAttempts = 0;
          rec.phoneNumber = parsePhoneNumber(user && user.id);
          rec.displayName = (user && user.name) || null;
          touch(id);
          log.info(`session connected`);
          fireEvent(id, { type: 'connected' });
        },
        onClose: async ({ loggedOut }) => {
          touch(id);
          if (loggedOut) {
            rec.status = 'logged_out';
            rec.qr = null;
            rec.handle = null;
            log.warn(`session logged out (unlinked from phone)`);
            fireEvent(id, { type: 'logged_out' });
            try {
              await wipeAuth(id);
            } catch (_) { /* already logged inside wipeAuth */ }
            return;
          }
          rec.status = 'reconnecting';
          rec.handle = null;
          const waitMs = backoffDelayMs(rec.reconnectAttempts);
          rec.reconnectAttempts += 1;
          log.warn(`socket closed, reconnecting in ${waitMs}ms (attempt ${rec.reconnectAttempts})`);
          setTimeout(() => {
            // Only restart if nobody else did (e.g. explicit start/logout) and no concurrent open in progress.
            const current = sessions.get(id);
            if (current === rec && rec.status === 'reconnecting' && !starting.has(id)) {
              // Use starting guard to avoid 2 concurrent makeSocket for same id (caused conflict replaced)
              const p = openSocket(id, rec).catch((err) => {
                log.error(`reconnect failed: ${err.message}`);
              });
              starting.set(id, p);
              p.finally(() => starting.delete(id));
            }
          }, waitMs).unref?.();
        },
      },
    });
    rec.handle = handle;
    if (rec.status !== 'connected' && rec.status !== 'reconnecting') rec.status = 'starting';
    return rec;
  }

  async function start(id) {
    if (!ID_RE.test(id || '')) throw new InvalidConnectionId();
    const existing = sessions.get(id);
    if (existing && (existing.status === 'connected' || existing.status === 'awaiting_qr' || existing.status === 'starting' || existing.status === 'reconnecting')) {
      return snapshot(id, existing);
    }
    if (starting.has(id)) return starting.get(id).then(() => snapshot(id, sessions.get(id)));
    let rec = existing;
    if (!rec || rec.status === 'logged_out' || rec.status === 'closed') {
      if (rec && rec.status === 'logged_out') await wipeAuth(id);
      rec = {
        status: 'starting', handle: null, qr: null,
        phoneNumber: null, displayName: null, lastSeenAt: null, reconnectAttempts: 0,
      };
      sessions.set(id, rec);
    }
    const p = openSocket(id, rec)
      .then(() => snapshot(id, rec))
      .finally(() => starting.delete(id));
    starting.set(id, p);
    return p;
  }

  function status(id) {
    const rec = get(id);
    if (!rec) return null;
    return snapshot(id, rec);
  }

  function freshQr(id) {
    const rec = get(id);
    if (!rec || !rec.qr) return null;
    if (clock() >= rec.qr.expiresAt) return null;
    return { qrDataUrl: rec.qr.dataUrl, issuedAt: rec.qr.issuedAt, expiresAt: rec.qr.expiresAt };
  }

  async function logout(id) {
    const rec = get(id);
    if (rec && rec.handle) {
      try {
        await rec.handle.logout();
      } catch (err) {
        log.warn(`logout call failed: ${err.message}`);
      }
      try {
        await rec.handle.end();
      } catch (_) { /* best effort */ }
    }
    await wipeAuth(id);
    if (rec) {
      rec.handle = null;
      rec.qr = null;
      rec.status = 'logged_out';
      return snapshot(id, rec);
    }
    return { connectionId: id, status: 'logged_out' };
  }

  // Forced restart used by the watchdog and by explicit reconnect calls.
  // Never touches auth state (logged-out sessions need a re-scan, not a restart).
  async function forceReconnect(id) {
    if (!ID_RE.test(id || '')) throw new InvalidConnectionId();
    if (starting.has(id)) return starting.get(id).then(() => snapshot(id, sessions.get(id)));
    const rec = sessions.get(id);
    if (!rec || rec.status === 'logged_out') return rec ? snapshot(id, rec) : null;
    if (rec.handle) {
      try {
        await rec.handle.end();
      } catch (_) { /* best effort */ }
      rec.handle = null;
    }
    rec.status = 'reconnecting';
    touch(id);
    const p = openSocket(id, rec);
    starting.set(id, p);
    try {
      await p;
    } finally {
      starting.delete(id);
    }
    return snapshot(id, rec);
  }
  function list() {
    return [...sessions.entries()].map(([id, rec]) => snapshot(id, rec));
  }

  // Boot resume: restart every previously-paired session. Stored creds mean
  // Baileys opens without QR (acceptance: restart reconnects, no re-scan).
  async function resume() {
    const ids = await discoverPaired(authDir);
    const started = [];
    for (const id of ids) {
      try {
        await start(id);
        started.push(id);
      } catch (err) {
        log.warn(`resume failed for session: ${err.message}`);
      }
    }
    return started;
  }

  // Admin-initiated push (human handover): send plain text to a customer
  // chat on this connection's socket. Routes through the same paced,
  // retried outbox as inbound replies. Throws NO_SOCKET when offline.
  async function sendTo(id, chatJid, body, idemKey) {
    if (!ID_RE.test(id || '')) throw new InvalidConnectionId();
    const text = String(body || '').trim();
    if (!text) throw new Error('EMPTY_BODY');
    const jid = String(chatJid || '').trim();
    if (!jid) throw new Error('EMPTY_RECIPIENT');
    try {
      await replyVia(id, jid, { type: 'text', body: text.slice(0, 4096) }, idemKey || null);
    } catch (err) {
      noteTransportFailure(id, err);
      throw err;
    }
    touch(id);
    return { connectionId: id, sent: true };
  }

  // A failed transport await on a "connected" socket usually means it is
  // half-open (server will not close it for us). Nudge one background
  // reconnect, debounced — the next turn then rides a fresh socket instead
  // of timing out again. Never throws; never blocks the caller.
  function noteTransportFailure(id, err) {
    try {
      const rec = sessions.get(id);
      if (!rec) return;
      if (rec.status !== 'connected' && rec.status !== 'reconnecting') return;
      if (clock() - (rec.lastTransportHeal || 0) < SELF_HEAL_DEBOUNCE_MS) return;
      rec.lastTransportHeal = clock();
      log.warn(`transport failure (${(err && err.code) || (err && err.message) || 'unknown'}), healing socket`);
      forceReconnect(id).catch(() => {});
    } catch (_) { /* self-heal must never break sends */ }
  }

  async function closeAll() {    // Best-effort end of every live socket (shutdown path; P4 adds queue drain).
    await Promise.all([...sessions.values()].map(async (rec) => {
      if (rec.handle) {
        try {
          await rec.handle.end();
        } catch (_) { /* best effort */ }
        rec.handle = null;
      }
      if (rec.status === 'connected' || rec.status === 'reconnecting') {
        rec.status = 'reconnecting';
      }
    }));
  }

  return { start, status, freshQr, logout, list, get, resume, closeAll, forceReconnect, touch, handleInboundMessage, sendTo };
}

module.exports = { createSessionManager, InvalidConnectionId, parsePhoneNumber, backoffDelayMs };
