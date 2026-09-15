// Typing heartbeat: composing shows for the whole engine turn, paused after.
const assert = require('node:assert');
const test = require('node:test');

const { createSessionManager } = require('../src/sessionManager');

const ID_A = '11111111-2222-4333-8444-555555555555';
const DM = '919521433306@s.whatsapp.net';

function fakeManager({ deliverImpl, beatMs } = {}) {
  const presence = [];
  const mgr = createSessionManager({
    makeSocket: async () => ({
      async logout() {},
      async end() {},
      async sendRaw() {
        return { key: { id: 'S1' } };
      },
      async presence(chatJid, state) {
        presence.push({ chatJid, state });
      },
    }),
    useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
    authDir: '/tmp/wa-typing-test',
    deliverInbound: deliverImpl || (async () => ({ reply: null })),
    typingBeatMs: beatMs,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  return { mgr, presence };
}

function dmMsg(id, text) {
  return {
    key: { remoteJid: DM, fromMe: false, id },
    message: { conversation: text || 'Hi' },
    messageTimestamp: Math.floor(Date.now() / 1000),
  };
}

test('composing sent on accept, paused after reply', async () => {
  const { mgr, presence } = fakeManager({
    deliverImpl: async () => ({ reply: { type: 'text', body: 'ok' } }),
  });
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, dmMsg('MT1'), 'notify');
  assert.deepStrictEqual(out, { delivered: true, replied: true });
  assert.ok(presence.length >= 2, `expected composing+paused, got ${JSON.stringify(presence)}`);
  assert.deepStrictEqual(presence[0], { chatJid: DM, state: 'composing' });
  assert.deepStrictEqual(presence[presence.length - 1], { chatJid: DM, state: 'paused' });
});

test('composing refreshes on a beat during a slow backend turn', async () => {
  const { mgr, presence } = fakeManager({
    beatMs: 20,
    deliverImpl: async () => {
      await new Promise((r) => setTimeout(r, 75));
      return { reply: null };
    },
  });
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, dmMsg('MT2'), 'notify');
  assert.deepStrictEqual(out, { delivered: true, replied: false });
  const composing = presence.filter((p) => p.state === 'composing');
  assert.ok(composing.length >= 2, `expected beat refreshes, got ${JSON.stringify(presence)}`);
  assert.strictEqual(presence[presence.length - 1].state, 'paused');
});

test('paused sent when delivery exhausts', async () => {
  const { mgr, presence } = fakeManager({
    deliverImpl: async () => {
      const e = new Error('down');
      throw e;
    },
  });
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, dmMsg('MT3'), 'notify');
  assert.deepStrictEqual(out, { dropped: 'deliver-exhausted' });
  assert.strictEqual(presence[0].state, 'composing');
  assert.strictEqual(presence[presence.length - 1].state, 'paused');
});

test('no presence for ignored (non-text) messages', async () => {
  const { mgr, presence } = fakeManager();
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, {
    key: { remoteJid: DM, fromMe: false, id: 'MT4' },
    message: { messageContextInfo: {}, placeholderMessage: {} },
    messageTimestamp: Math.floor(Date.now() / 1000),
  }, 'notify');
  assert.deepStrictEqual(out, { dropped: 'no-text' });
  assert.strictEqual(presence.length, 0);
});
