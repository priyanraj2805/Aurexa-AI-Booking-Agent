// Transport-hang hardening: no await on a half-open socket may stall a
// turn or wedge the outbox FIFO. Failures self-heal the socket (debounced).
const assert = require('node:assert');
const test = require('node:test');

const { sendViaHandle, withTimeout } = require('../src/send');
const { createSessionManager } = require('../src/sessionManager');
const { createOutbox } = require('../src/outbox');

const hang = () => new Promise(() => {});
const ID_A = '11111111-2222-4333-8444-555555555555';
const DM = '919521433306@s.whatsapp.net';

test('withTimeout rejects hung promises fast', async () => {
  const t0 = Date.now();
  await assert.rejects(withTimeout(hang(), 20, 'HUNG'), /HUNG/);
  assert.ok(Date.now() - t0 < 1000);
});

test('sendViaHandle survives hanging presence and still sends', async () => {
  const sent = [];
  const handle = {
    async presence() { await hang(); },
    async sendRaw(chatJid, payload) { sent.push({ chatJid, payload }); return { key: { id: 'S1' } }; },
  };
  const t0 = Date.now();
  const res = await sendViaHandle(handle, DM, { type: 'text', body: 'Hi' },
    { timeouts: { presenceMs: 20, sendMs: 500 } });
  assert.strictEqual(res.key.id, 'S1');
  assert.strictEqual(sent.length, 1);
  assert.ok(Date.now() - t0 < 2000, 'presence hang must not stall the send');
});

test('sendViaHandle times out a hung sendRaw', async () => {
  const handle = {
    async presence() {},
    async sendRaw() { await hang(); },
  };
  const t0 = Date.now();
  await assert.rejects(
    sendViaHandle(handle, DM, { type: 'text', body: 'Hi' }, { timeouts: { sendMs: 20 } }),
    /SEND_TIMEOUT/
  );
  assert.ok(Date.now() - t0 < 2000);
});

test('outbox drops a hung task and keeps serving the queue', async () => {
  const sent = [];
  const outbox = createOutbox({
    minGapMs: 1,
    maxPerMinute: 1000,
    maxAttempts: 1,
    taskTimeoutMs: 20,
    delay: async () => {},
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  const hung = outbox.enqueue(ID_A, 'k-hung', async () => { await hang(); });
  const good = outbox.enqueue(ID_A, 'k-good', async () => { sent.push(1); return 'ok'; });
  await assert.rejects(hung, /TASK_TIMEOUT/);
  const res = await good;
  assert.strictEqual(res.result, 'ok');
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(outbox.depth(ID_A), 0);
});

function hangingPresenceManager(events) {
  return createSessionManager({
    makeSocket: async () => ({
      async logout() {},
      async end() {},
      async sendRaw(chatJid, payload) {
        events.push({ sendRaw: true });
        return { key: { id: 'S1' } };
      },
      // Presence wedge: the half-open socket signature.
      async presence() { await hang(); },
    }),
    useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
    authDir: '/tmp/wa-presence-hang-test',
    outbox: null,
    deliverInbound: async () => ({ reply: { type: 'text', body: 'Ack' } }),
    logger: { info: (m) => events.push({ log: m }), warn: () => {}, error: () => {} },
  });
}

test('inbound survives a wedged presence stanza and still delivers', async () => {
  const events = [];
  const mgr = hangingPresenceManager(events);
  await mgr.start(ID_A);
  const t0 = Date.now();
  const r = await mgr.handleInboundMessage(ID_A, {
    key: { remoteJid: DM, fromMe: false, id: 'M-HANG-1' },
    message: { conversation: 'Hi' },
    messageTimestamp: Math.floor(Date.now() / 1000),
  });
  // Default presence budget is 3s (fast norm) — the turn must complete.
  assert.ok(Date.now() - t0 < 30000, 'presence wedge must not stall the turn');
  assert.deepStrictEqual(r, { delivered: true, replied: true });
  assert.ok(events.some((e) => e.sendRaw), 'reply still sent');
});

test('repeated transport failures heal the socket once per debounce window', async () => {
  let nowMs = 1000000;
  let sockets = 0;
  const mgr = createSessionManager({
    makeSocket: async ({ hooks }) => {
      sockets += 1;
      setImmediate(() => hooks.onOpen({ id: '1@s.whatsapp.net', name: 'Biz' }));
      return {
        async logout() {},
        async end() {},
        async sendRaw() { throw new Error('socket wedged'); },
        async presence() {},
      };
    },
    useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
    authDir: '/tmp/wa-heal-test',
    outbox: null,
    now: () => nowMs,
    deliverInbound: async () => null,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  await mgr.start(ID_A);
  await new Promise((r) => setTimeout(r, 20)); // let onOpen land
  assert.strictEqual(sockets, 1);
  // Two rapid failures: one heal (second debounced).
  await assert.rejects(mgr.sendTo(ID_A, DM, 'one', 'idem-1'), /wedged/);
  await new Promise((r) => setTimeout(r, 50)); // let the heal reconnect land
  assert.strictEqual(sockets, 2);
  await assert.rejects(mgr.sendTo(ID_A, DM, 'two', 'idem-2'), /wedged/);
  assert.strictEqual(sockets, 2);
  // Past the debounce window: heals again (heal runs in background).
  nowMs += 31 * 1000;
  await assert.rejects(mgr.sendTo(ID_A, DM, 'three', 'idem-3'), /wedged/);
  await new Promise((r) => setTimeout(r, 100));
  assert.strictEqual(sockets, 3);
});
