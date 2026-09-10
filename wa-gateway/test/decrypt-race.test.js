// Decryption-race recovery (P11): placeholder upserts dropped, decrypted
// messages.update forwarded through the SAME pipeline, redelivery safe.
const assert = require('node:assert');
const test = require('node:test');

const { createSessionManager } = require('../src/sessionManager');
const { createOutbox } = require('../src/outbox');

const ID_A = '11111111-2222-4333-8444-555555555555';
const DM = '919521433306@s.whatsapp.net';
const LID_DM = '123456789012345@lid';

function fakeManager(deliverImpl) {
  const delivered = [];
  const sent = [];
  const mgr = createSessionManager({
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
    authDir: '/tmp/wa-decrypt-race-test',
    deliverInbound: async (id, envelope) => {
      delivered.push(envelope);
      return deliverImpl(id, envelope);
    },
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  return { mgr, delivered, sent };
}

function textMsg(id, text, jid) {
  return {
    key: { remoteJid: jid || DM, fromMe: false, id },
    message: { conversation: text },
    messageTimestamp: Math.floor(Date.now() / 1000),
  };
}

function placeholderMsg(id) {
  // What Baileys emits on upsert before keys sync (real prod shape).
  return {
    key: { remoteJid: LID_DM, fromMe: false, id },
    message: { messageContextInfo: {}, placeholderMessage: {} },
    messageTimestamp: Math.floor(Date.now() / 1000),
  };
}

// A. upsert with decrypted text is forwarded normally.
test('A: notify upsert with text is delivered to the backend', async () => {
  const { mgr, delivered } = fakeManager(async () => ({ reply: null }));
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, textMsg('MA1', 'Hello'), 'notify');
  assert.deepStrictEqual(out, { delivered: true, replied: false });
  assert.strictEqual(delivered.length, 1);
  assert.strictEqual(delivered[0].text, 'Hello');
  assert.strictEqual(delivered[0].messageId, 'MA1');
});

// B. placeholder-only upsert is NOT forwarded as customer text.
test('B: placeholder-only upsert is dropped as no-text', async () => {
  const { mgr, delivered } = fakeManager(async () => ({ reply: null }));
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, placeholderMsg('MB1'), 'notify');
  assert.deepStrictEqual(out, { dropped: 'no-text' });
  assert.strictEqual(delivered.length, 0);
});

// C(i). messages.update subscription routes decrypted content via onMessage.
test('C-sub: messages.update with decrypted text reaches onMessage as update', async () => {
  const baileysPath = require.resolve('@whiskeysockets/baileys');
  const realCached = require.cache[baileysPath];
  const handlers = {};
  const fakeSock = { ev: { on: (evt, fn) => { handlers[evt] = fn; } } };
  require.cache[baileysPath] = {
    id: baileysPath, filename: baileysPath, loaded: true,
    exports: {
      default: () => fakeSock,
      fetchLatestBaileysVersion: async () => ({ version: [2, 3000, 0] }),
    },
  };
  try {
    delete require.cache[require.resolve('../src/baileys.js')];
    const { defaultMakeSocket } = require('../src/baileys.js');
    const calls = [];
    await defaultMakeSocket({
      authDir: '/tmp/wa-decrypt-race-test',
      state: {},
      saveCreds: async () => {},
      hooks: {
        onMessage: async (msg, source) => { calls.push({ msg, source }); },
        onQr: async () => {},
        onOpen: async () => {},
        onClose: async () => {},
      },
      log: { info: () => {}, warn: () => {}, error: () => {} },
    });
    assert.ok(typeof handlers['messages.upsert'] === 'function', 'upsert still subscribed');
    assert.ok(typeof handlers['messages.update'] === 'function', 'update subscribed');

    // Status-only update (no decrypted content) must not fire the pipeline.
    handlers['messages.update']([{ key: { remoteJid: DM, id: 'MS1' }, update: { status: 3 } }]);
    // Malformed payloads must not throw.
    handlers['messages.update'](undefined);
    handlers['messages.update']([{ key: null }]);
    await new Promise((r) => setImmediate(r));
    assert.strictEqual(calls.length, 0);

    // Decrypted content update fires once with source 'update', key preserved.
    handlers['messages.update']([{
      key: { remoteJid: DM, fromMe: false, id: 'MC1' },
      update: {
        message: { conversation: 'Hello decrypted' },
        messageTimestamp: 1700000000,
        pushName: 'Cust',
      },
    }]);
    await new Promise((r) => setImmediate(r));
    assert.strictEqual(calls.length, 1);
    assert.strictEqual(calls[0].source, 'update');
    assert.strictEqual(calls[0].msg.message.conversation, 'Hello decrypted');
    assert.strictEqual(calls[0].msg.key.id, 'MC1');
    assert.strictEqual(calls[0].msg.messageTimestamp, 1700000000);
  } finally {
    if (realCached) require.cache[baileysPath] = realCached;
    else delete require.cache[baileysPath];
  }
});

// C(ii). update-sourced decrypted text flows through the manager pipeline.
test('C-pipe: update source with text is delivered', async () => {
  const { mgr, delivered } = fakeManager(async () => ({ reply: null }));
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, textMsg('MC2', 'Hi again'), 'update');
  assert.deepStrictEqual(out, { delivered: true, replied: false });
  assert.strictEqual(delivered.length, 1);
  assert.strictEqual(delivered[0].messageId, 'MC2');
});

// D. Same message ID via upsert then update: redelivery is absorbed
// downstream (backend is_duplicate + outbox idem key) — the customer still
// gets exactly ONE reply. Same contract as send.test.js redelivery case.
test('D: duplicate upsert+update for one ID yields a single customer reply', async () => {
  let calls = 0;
  const sent = [];
  const outbox = createOutbox({
    minGapMs: 1,
    maxPerMinute: 1000,
    delay: async () => {},
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  const mgr = createSessionManager({
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
    authDir: '/tmp/wa-decrypt-race-test',
    outbox,
    deliverInbound: async (id, envelope) => {
      calls += 1;
      // Both paths must expose the SAME backend dedup key (req 9).
      assert.strictEqual(envelope.messageId, 'MD1');
      return { reply: { type: 'text', body: 'Ack' } };
    },
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  await mgr.start(ID_A);
  const first = await mgr.handleInboundMessage(ID_A, textMsg('MD1', 'Hi'), 'notify');
  assert.deepStrictEqual(first, { delivered: true, replied: true });
  const second = await mgr.handleInboundMessage(ID_A, textMsg('MD1', 'Hi'), 'update');
  assert.deepStrictEqual(second, { delivered: true, replied: true });
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(sent[0].payload.text, 'Ack');
});

// D2. Repeated identical updates: still exactly one customer reply.
test('D2: repeated identical updates yield a single customer reply', async () => {
  const sent = [];
  const outbox = createOutbox({
    minGapMs: 1,
    maxPerMinute: 1000,
    delay: async () => {},
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  const mgr = createSessionManager({
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
    authDir: '/tmp/wa-decrypt-race-test',
    outbox,
    deliverInbound: async () => ({ reply: { type: 'text', body: 'Ack' } }),
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  await mgr.start(ID_A);
  await mgr.handleInboundMessage(ID_A, textMsg('MD2', 'Yo'), 'update');
  const again = await mgr.handleInboundMessage(ID_A, textMsg('MD2', 'Yo'), 'update');
  assert.deepStrictEqual(again, { delivered: true, replied: true });
  assert.strictEqual(sent.length, 1);
});

// E. Newsletter/broadcast stay ignored even via the update path.
test('E: newsletter and broadcast updates remain ignored', async () => {
  const { mgr, delivered } = fakeManager(async () => ({ reply: null }));
  await mgr.start(ID_A);
  const nl = await mgr.handleInboundMessage(
    ID_A, textMsg('ME1', 'spam', '12345@newsletter'), 'update');
  assert.deepStrictEqual(nl, { dropped: 'newsletter' });
  const bc = await mgr.handleInboundMessage(
    ID_A, textMsg('ME2', 'spam', 'status@broadcast'), 'update');
  assert.deepStrictEqual(bc, { dropped: 'broadcast' });
  assert.strictEqual(delivered.length, 0);
});

// Placeholder never marks an ID: later update for the same ID still flows.
test('placeholder does not poison the later decrypted update', async () => {
  let calls = 0;
  const { mgr } = fakeManager(async () => {
    calls += 1;
    return { reply: null };
  });
  await mgr.start(ID_A);
  const dropped = await mgr.handleInboundMessage(ID_A, placeholderMsg('MP1'), 'notify');
  assert.deepStrictEqual(dropped, { dropped: 'no-text' });
  const flowed = await mgr.handleInboundMessage(ID_A, textMsg('MP1', 'Real text', LID_DM), 'update');
  assert.deepStrictEqual(flowed, { delivered: true, replied: false });
  assert.strictEqual(calls, 1);
});
