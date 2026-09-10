// Baileys adapter — the ONLY file that imports @whiskeysockets/baileys.
// Everything else talks to plain { makeSocket, useAuthState } interfaces so
// session logic stays unit-testable with fakes.
const path = require('node:path');

const BROWSER = ['Aurexa WA Gateway', 'Chrome', '1.0.0'];

// Baileys DisconnectReason.loggedOut — kept as a literal so this module is
// the single place that must agree with the installed Baileys major version.
const LOGGED_OUT_STATUS = 401;

// QR codes rotate roughly every minute; treat cached QR as fresh for 55s.
const QR_FRESH_MS = 55 * 1000;

async function defaultUseAuthState(authDir) {
  const { useMultiFileAuthState } = require('@whiskeysockets/baileys');
  return useMultiFileAuthState(authDir);
}

async function defaultMakeSocket({ authDir, state, saveCreds, hooks, log }) {
  const baileys = require('@whiskeysockets/baileys');
  const makeWASocket = baileys.default;
  const { jidKind, last4, contentKeys } = require('./diag');
  const logFn = log || { info: () => {}, warn: () => {}, error: () => {} };
  // Pin to the current server-advertised version: WhatsApp drops the
  // registration handshake for stale hardcoded client versions (no QR then).
  let version;
  try {
    ({ version } = await baileys.fetchLatestBaileysVersion());
  } catch (_) {
    version = undefined; // offline fallback: bundled default
  }
  let childLogger;
  try {
    childLogger = require('pino')({ level: 'warn' });
  } catch (_) {
    childLogger = undefined;
  }
  const sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger: childLogger,
    browser: BROWSER,
    syncFullHistory: false,
    markOnlineOnConnect: false,
    connectTimeoutMs: 60 * 1000,
    defaultQueryTimeoutMs: 60 * 1000,
  });
  // Keep the auth dir beside the wiring for future file-permission audits.
  void path.basename(authDir);
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', (update) => {
    if (update.qr) {
      Promise.resolve()
        .then(() => hooks.onQr(update.qr))
        .catch(() => {});
    }
    if (update.connection === 'open') {
      const id = sock.user && sock.user.id ? sock.user.id : null;
      const name = sock.user && sock.user.name ? sock.user.name : null;
      Promise.resolve()
        .then(() => hooks.onOpen({ id, name }))
        .catch(() => {});
    }
    if (update.connection === 'close') {
      const code = update.lastDisconnect && update.lastDisconnect.error
        && update.lastDisconnect.error.output
        ? update.lastDisconnect.error.output.statusCode
        : null;
      Promise.resolve()
        .then(() => hooks.onClose({ loggedOut: code === LOGGED_OUT_STATUS, statusCode: code }))
        .catch(() => {});
    }
  });
  // Inbound customer messages (P7). Only 'notify' (new live messages);
  // normalization + filtering lives in inbound.js via hooks.onMessage.
  sock.ev.on('messages.upsert', (upsert) => {
    if (!upsert || !Array.isArray(upsert.messages)) return;
    const source = upsert.type === 'append' ? 'append' : 'notify';
    // Boundary log: receipt census only (no content, no full JIDs).
    try {
      const census = upsert.messages.map((m) => ({
        fromMe: !!(m.key && m.key.fromMe),
        jid: m.key && m.key.remoteJid ? `${jidKind(m.key.remoteJid)}:${last4(m.key.remoteJid)}` : 'none',
        keys: contentKeys(m.message),
      }));
      logFn.info(`upsert source=${source} count=${census.length} detail=${JSON.stringify(census)}`);
    } catch (_) { /* logging must never break receipt */ }
    for (const msg of upsert.messages) {
      Promise.resolve()
        .then(() => hooks.onMessage(msg, source))
        .catch(() => {});
    }
  });
  // Decryption-race recovery: Baileys first emits undecryptable DMs on
  // 'messages.upsert' as placeholderMessage stubs (dropped as no-text by
  // inbound.js), then delivers the decrypted content via 'messages.update'.
  // Route updates through the SAME pipeline so the text is processed exactly
  // once content exists. Status-only updates (no update.message) are skipped.
  // Backend dedup on message ID plus the gateway forwarded-ID guard make
  // upsert+update redelivery safe.
  sock.ev.on('messages.update', (updates) => {
    if (!Array.isArray(updates)) return;
    const withContent = updates.filter(
      (u) => u && u.key && u.update && u.update.message && typeof u.update.message === 'object'
    );
    if (withContent.length === 0) return;
    try {
      const census = withContent.map((u) => ({
        keyId: u.key && u.key.id ? String(u.key.id).slice(0, 24) : 'none',
        jid: u.key && u.key.remoteJid ? `${jidKind(u.key.remoteJid)}:${last4(u.key.remoteJid)}` : 'none',
        keys: contentKeys(u.update.message),
      }));
      logFn.info(`update count=${census.length} detail=${JSON.stringify(census)}`);
    } catch (_) { /* logging must never break receipt */ }
    for (const u of withContent) {
      const msg = {
        key: u.key,
        message: u.update.message,
        messageTimestamp: u.update.messageTimestamp,
        pushName: u.update.pushName,
      };
      Promise.resolve()
        .then(() => hooks.onMessage(msg, 'update'))
        .catch(() => {});
    }
  });
  return {
    // Logout removes this linked device server-side (remove-companion-device
    // IQ) and only then closes the socket: ending first would drop the
    // logout stanza and leave a stale entry in the phone's Linked Devices.
    logout: () => logoutSocket(sock),
    end: async () => { try { if (typeof sock.end === 'function') await sock.end(undefined); } catch (_) { /* best effort */ } },
    // Raw transport primitives for the send layer (src/send.js).
    sendRaw: async (chatJid, payload) => sock.sendMessage(chatJid, payload),
    presence: async (chatJid, state) => {
      if (typeof sock.sendPresenceUpdate === 'function') {
        await sock.sendPresenceUpdate(state, chatJid);
      }
    },
  };
}

// Grace period lets the remove-companion-device stanza flush before the
// socket closes. Extracted (and delay-injectable) so ordering is unit-tested.
async function logoutSocket(sock, delayMs = 2000, delayFn) {
  const wait = delayFn || ((ms) => new Promise((r) => setTimeout(r, ms)));
  try {
    await sock.logout();
  } catch (_) { /* already closed — fall through to end() */ }
  await wait(delayMs);
  try {
    if (typeof sock.end === 'function') await sock.end(undefined);
  } catch (_) { /* best effort */ }
}

module.exports = {
  defaultUseAuthState,
  defaultMakeSocket,
  logoutSocket,
  LOGGED_OUT_STATUS,
  QR_FRESH_MS,
};
