// Human-handover push tests: manager.sendTo + POST /sessions/:id/send.
const assert = require('node:assert');
const test = require('node:test');

const { createSessionManager } = require('../src/sessionManager');
const { createOutbox } = require('../src/outbox');

const ID_A = '11111111-2222-4333-8444-555555555555';
const DM = '919521433306@s.whatsapp.net';

function makeManager(sent) {
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
    authDir: '/tmp/wa-sendto-test',
    outbox,
    deliverInbound: async () => null,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  return mgr;
}

test('sendTo pushes plain text through the outbox', async () => {
  const sent = [];
  const mgr = makeManager(sent);
  await mgr.start(ID_A);
  const out = await mgr.sendTo(ID_A, DM, 'Hello from support');
  assert.deepStrictEqual(out, { connectionId: ID_A, sent: true });
  assert.strictEqual(sent.length, 1);
  assert.strictEqual(sent[0].chatJid, DM);
  assert.strictEqual(sent[0].payload.text, 'Hello from support');
});

test('sendTo rejects bad ids, empty body and offline sockets', async () => {
  const sent = [];
  const mgr = makeManager(sent);
  await assert.rejects(() => mgr.sendTo('not-a-uuid', DM, 'hi'), /Invalid connection id/);
  await assert.rejects(() => mgr.sendTo(ID_A, DM, '   '), /EMPTY_BODY/);
  await assert.rejects(() => mgr.sendTo(ID_A, '   ', 'hi'), /EMPTY_RECIPIENT/);
  // Unknown (never-started) session -> NO_SOCKET
  await assert.rejects(
    () => mgr.sendTo('22222222-2222-4333-8444-555555555555', DM, 'hi'),
    /NO_SOCKET/
  );
  assert.strictEqual(sent.length, 0);
});

test('POST /sessions/:id/send requires key and validates payload', async () => {
  const { createApp } = require('../src/index');
  process.env.WA_GATEWAY_SERVICE_KEY = 'test-key-handover';
  const sent = [];
  const outbox = createOutbox({
    minGapMs: 1,
    maxPerMinute: 1000,
    delay: async () => {},
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
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
    authDir: '/tmp/wa-sendroute-test',
    outbox,
    deliverInbound: async () => null,
  });
  const manager = app.get('wa.manager');
  await manager.start(ID_A);

  const srv = app.listen(0);
  await new Promise((r) => srv.on('listening', r));
  const port = srv.address().port;
  try {
    // No key -> 401
    let res = await fetch(`http://127.0.0.1:${port}/sessions/${ID_A}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to: DM, body: 'hi' }),
    });
    assert.strictEqual(res.status, 401);
    // Bad payload -> 400
    res = await fetch(`http://127.0.0.1:${port}/sessions/${ID_A}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gateway-Key': 'test-key-handover' },
      body: JSON.stringify({ to: DM, body: '   ' }),
    });
    assert.strictEqual(res.status, 400);
    // Happy path -> 202, message lands on the socket
    res = await fetch(`http://127.0.0.1:${port}/sessions/${ID_A}/send`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Gateway-Key': 'test-key-handover' },
      body: JSON.stringify({ to: DM, body: 'Support here' }),
    });
    assert.strictEqual(res.status, 202);
    assert.strictEqual(sent.length, 1);
    assert.strictEqual(sent[0].payload.text, 'Support here');
    // Offline session -> 409
    res = await fetch(
      `http://127.0.0.1:${port}/sessions/33333333-3333-4333-8444-555555555555/send`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Gateway-Key': 'test-key-handover' },
        body: JSON.stringify({ to: DM, body: 'hi' }),
      }
    );
    assert.strictEqual(res.status, 409);
  } finally {
    await new Promise((r) => srv.close(r));
  }
});
