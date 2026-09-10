// Catch-up ('append') inbound handling: fresh messages processed, stale dropped.
const assert = require('node:assert');
const test = require('node:test');

const { createSessionManager } = require('../src/sessionManager');

const ID_A = '11111111-2222-4333-8444-555555555555';
const DM = '919521433306@s.whatsapp.net';

function fakeManager(deliverImpl) {
  const sent = [];
  const mgr = createSessionManager({
    makeSocket: async ({ hooks }) => {
      const h = {
        hooks,
        async logout() {},
        async end() {},
        async sendRaw(chatJid, payload) {
          sent.push({ chatJid, payload });
          return { key: { id: 'S1' } };
        },
        async presence() {},
      };
      return h;
    },
    useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
    authDir: '/tmp/wa-catchup-test',
    deliverInbound: deliverImpl,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  return { mgr, sent };
}

function dmMsg(id, ageSec) {
  return {
    key: { remoteJid: DM, fromMe: false, id },
    message: { conversation: 'Hi again' },
    messageTimestamp: Math.floor(Date.now() / 1000) - ageSec,
  };
}

test('fresh append messages are delivered; stale ones are dropped', async () => {
  let calls = 0;
  const { mgr, sent } = fakeManager(async () => {
    calls += 1;
    return { reply: { type: 'text', body: 'ok' } };
  });
  await mgr.start(ID_A);

  const outFresh = await mgr.handleInboundMessage(ID_A, dmMsg('M-fresh', 30), 'append');
  assert.deepStrictEqual(outFresh, { delivered: true, replied: true });

  const outStale = await mgr.handleInboundMessage(ID_A, dmMsg('M-stale', 3600), 'append');
  assert.deepStrictEqual(outStale, { dropped: 'stale-append' });

  assert.strictEqual(calls, 1);
  assert.strictEqual(sent.length, 1);
});

test('notify source needs no freshness check', async () => {
  let calls = 0;
  const { mgr } = fakeManager(async () => {
    calls += 1;
    return { reply: null };
  });
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, dmMsg('M-n', 99999));
  assert.deepStrictEqual(out, { delivered: true, replied: false });
  assert.strictEqual(calls, 1);
});
