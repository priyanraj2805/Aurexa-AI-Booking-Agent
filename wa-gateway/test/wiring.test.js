// Regression test: the production manager MUST deliver inbound messages to
// the backend by default. A past bug left deliverInbound unwired, so every
// customer message was silently "delivered" to a no-op and never answered.
const assert = require('node:assert');
const test = require('node:test');

const { createApp } = require('../src/index.js');
const { deliverToBackend } = require('../src/index.js');

const ID_A = '11111111-2222-4333-8444-555555555555';
const DM = '919521433306@s.whatsapp.net';

test('default manager wiring POSTs inbound to the backend and sends the reply', async () => {
  process.env.WA_GATEWAY_SERVICE_KEY = 'wiring-test-key';
  const realFetch = global.fetch;
  const calls = [];
  global.fetch = async (url, opts) => {
    calls.push({ url, headers: opts.headers, body: JSON.parse(opts.body) });
    return {
      ok: true,
      status: 200,
      json: async () => ({ status: 'ok', reply: { type: 'text', body: 'Hi there' } }),
    };
  };
  try {
    const sent = [];
    const app = createApp({
      makeSocket: async () => ({
        async logout() {},
        async end() {},
        async sendRaw(chatJid, payload) {
          sent.push({ chatJid, payload });
          return { key: { id: 'S1' } };
        },
        async presence() {},
      }),
      useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
      authDir: '/tmp/wa-wiring-test',
    });
    const manager = app.get('wa.manager');
    await manager.start(ID_A);
    const out = await manager.handleInboundMessage(ID_A, {
      key: { remoteJid: DM, fromMe: false, id: 'MWIRE1' },
      message: { conversation: 'Hi' },
      messageTimestamp: Math.floor(Date.now() / 1000),
    });
    assert.deepStrictEqual(out, { delivered: true, replied: true });
    assert.strictEqual(calls.length, 1);
    assert.ok(calls[0].url.endsWith('/internal/whatsapp-qr/inbound'), calls[0].url);
    assert.strictEqual(calls[0].headers['X-Gateway-Key'], 'wiring-test-key');
    assert.strictEqual(calls[0].body.connectionId, ID_A);
    assert.strictEqual(calls[0].body.text, 'Hi');
    assert.strictEqual(sent.length, 1);
    assert.strictEqual(sent[0].payload.text, 'Hi there');
  } finally {
    global.fetch = realFetch;
    delete process.env.WA_GATEWAY_SERVICE_KEY;
  }
});

test('backend 404 surfaces as unknown-connection without retry storm', async () => {
  process.env.WA_GATEWAY_SERVICE_KEY = 'wiring-test-key';
  const realFetch = global.fetch;
  let calls = 0;
  global.fetch = async () => {
    calls += 1;
    return { ok: false, status: 404, json: async () => ({}) };
  };
  try {
    const app = createApp({
      makeSocket: async () => ({
        async logout() {},
        async end() {},
        async sendRaw() {
          throw new Error('must not send');
        },
        async presence() {},
      }),
      useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
      authDir: '/tmp/wa-wiring-test',
    });
    const manager = app.get('wa.manager');
    await manager.start(ID_A);
    const out = await manager.handleInboundMessage(ID_A, {
      key: { remoteJid: DM, fromMe: false, id: 'MWIRE2' },
      message: { conversation: 'Hi' },
      messageTimestamp: Math.floor(Date.now() / 1000),
    });
    assert.deepStrictEqual(out, { dropped: 'unknown-connection' });
    assert.strictEqual(calls, 1);
  } finally {
    global.fetch = realFetch;
    delete process.env.WA_GATEWAY_SERVICE_KEY;
  }
});

test('deliverToBackend times out instead of hanging forever', async () => {
  process.env.WA_GATEWAY_SERVICE_KEY = 'wiring-test-key';
  process.env.BACKEND_TIMEOUT_MS = '50';
  const realFetch = global.fetch;
  // Mirror real fetch abort semantics: reject with AbortError on abort.
  global.fetch = (url, opts) => new Promise((_, reject) => {
    opts.signal.addEventListener('abort', () => {
      const e = new Error('aborted');
      e.name = 'AbortError';
      reject(e);
    });
  });
  try {
    await assert.rejects(
      deliverToBackend('11111111-2222-4333-8444-555555555555', { text: 'Hi' }),
      (e) => e.code === 'BACKEND_TIMEOUT'
    );
  } finally {
    global.fetch = realFetch;
    delete process.env.WA_GATEWAY_SERVICE_KEY;
    delete process.env.BACKEND_TIMEOUT_MS;
  }
});
