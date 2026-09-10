// P7 tests: inbound normalization + delivery flow.
const assert = require('node:assert');
const test = require('node:test');

const { normalizeInbound } = require('../src/inbound');
const { createSessionManager } = require('../src/sessionManager');

const DM = '919521433306@s.whatsapp.net';
function baseMsg(over) {
  return {
    key: { remoteJid: DM, fromMe: false, id: 'MSG1' },
    message: { conversation: 'Hi' },
    messageTimestamp: 1700000000,
    pushName: 'Rahul',
    ...(over || {}),
  };
}

test('normalizeInbound accepts plain and interactive texts', () => {
  assert.strictEqual(normalizeInbound(baseMsg()).envelope.text, 'Hi');
  assert.strictEqual(
    normalizeInbound(baseMsg({ message: { extendedTextMessage: { text: '  Hey  ' } } })).envelope.text,
    'Hey'
  );
  assert.strictEqual(
    normalizeInbound(baseMsg({ message: { buttonsResponseMessage: { selectedDisplayText: 'Confirm' } } })).envelope.text,
    'Confirm'
  );
  assert.strictEqual(
    normalizeInbound(baseMsg({ message: { listResponseMessage: { title: '10:00 AM' } } })).envelope.text,
    '10:00 AM'
  );
  const env = normalizeInbound(baseMsg()).envelope;
  assert.strictEqual(env.from, '919521433306');
  assert.strictEqual(env.chatJid, DM);
  assert.strictEqual(env.messageId, 'MSG1');
});

test('normalizeInbound ignores groups/status/broadcasts/own/protocol/empty', () => {
  const cases = [
    [{ key: { remoteJid: '1203@g.us', fromMe: false, id: 'x' }, message: { conversation: 'hi' } }, 'group'],
    [{ key: { remoteJid: 'status@broadcast', fromMe: false, id: 'x' }, message: { conversation: 'hi' } }, 'broadcast'],
    [{ key: { remoteJid: '1@newsletter', fromMe: false, id: 'x' }, message: { conversation: 'hi' } }, 'newsletter'],
    [{ key: { remoteJid: DM, fromMe: true, id: 'x' }, message: { conversation: 'hi' } }, 'from-me'],
    [{ key: { remoteJid: DM, fromMe: false, id: 'x' }, message: { protocolMessage: {} } }, 'protocol'],
    [{ key: { remoteJid: DM, fromMe: false, id: 'x' }, message: { imageMessage: {} } }, 'no-text'],
    [{ key: { remoteJid: DM, fromMe: false, id: 'x' }, message: { conversation: '   ' } }, 'no-text'],
    [{ key: { remoteJid: '12@s.whatsapp.net', fromMe: false, id: 'x' }, message: { conversation: 'hi' } }, 'bad-sender'],
  ];
  for (const [msg, reason] of cases) {
    assert.strictEqual(normalizeInbound(msg).skip, reason, JSON.stringify(msg));
  }
});

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
    authDir: '/tmp/wa-inbound-test',
    deliverInbound: deliverImpl,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  return { mgr, sent };
}

const ID_A = '11111111-2222-4333-8444-555555555555';

test('handleInboundMessage delivers, then replies with backend text', async () => {
  const seen = [];
  const { mgr, sent } = fakeManager(async (id, env) => {
    seen.push([id, env.text]);
    return { reply: { type: 'text', body: 'Hello back' } };
  });
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, baseMsg());
  assert.deepStrictEqual(out, { delivered: true, replied: true });
  assert.deepStrictEqual(seen, [[ID_A, 'Hi']]);
  assert.deepStrictEqual(sent, [{ chatJid: DM, payload: { text: 'Hello back' } }]);
});

test('skipped messages never reach deliver', async () => {  let calls = 0;
  const { mgr } = fakeManager(async () => { calls += 1; return null; });
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, baseMsg({
    key: { remoteJid: '1203@g.us', fromMe: false, id: 'x' },
    message: { conversation: 'hi' },
  }));
  assert.deepStrictEqual(out, { dropped: 'group' });
  assert.strictEqual(calls, 0);
});

test('deliver retries with backoff then drops loudly', async () => {
  let calls = 0;
  const { mgr } = fakeManager(async () => {
    calls += 1;
    throw new Error('down');
  });
  await mgr.start(ID_A);
  const t0 = Date.now();
  const out = await mgr.handleInboundMessage(ID_A, baseMsg());
  assert.deepStrictEqual(out, { dropped: 'deliver-exhausted' });
  assert.strictEqual(calls, 3);
  assert.ok(Date.now() - t0 > 2000, 'backed off across attempts');
});

test('unknown-connection at backend is never retried', async () => {
  let calls = 0;
  const { mgr } = fakeManager(async () => {
    calls += 1;
    const err = new Error('gone');
    err.code = 'UNKNOWN_CONNECTION';
    throw err;
  });
  await mgr.start(ID_A);
  const out = await mgr.handleInboundMessage(ID_A, baseMsg());
  assert.deepStrictEqual(out, { dropped: 'unknown-connection' });
  assert.strictEqual(calls, 1);
});
