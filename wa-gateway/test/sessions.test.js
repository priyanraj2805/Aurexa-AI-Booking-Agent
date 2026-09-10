// P2 tests: Baileys QR pairing lifecycle with fake sockets (no network).
const assert = require('node:assert');
const test = require('node:test');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs/promises');

const { createSessionManager, InvalidConnectionId, parsePhoneNumber } = require('../src/sessionManager');
const { createApp } = require('../src/index.js');

const ID_A = '11111111-2222-4333-8444-555555555555';
const ID_B = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';

function makeDeps(authDir) {
  const created = [];
  const dirs = [];
  async function makeSocket({ hooks }) {
    const handle = {
      hooks,
      logoutCalls: 0,
      endCalls: 0,
      async logout() { handle.logoutCalls += 1; },
      async end() { handle.endCalls += 1; },
    };
    created.push(handle);
    return handle;
  }
  async function useAuthState(dir) {
    dirs.push(dir);
    return { state: {}, saveCreds: async () => {} };
  }
  return { makeSocket, useAuthState, created, dirs };
}

function managerWithTmp(extra) {
  const authDir = path.join(os.tmpdir(), `wa-test-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);
  const deps = makeDeps(authDir);
  const mgr = createSessionManager({
    makeSocket: deps.makeSocket,
    useAuthState: deps.useAuthState,
    authDir,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
    ...(extra || {}),
  });
  return { mgr, deps, authDir };
}

test('parsePhoneNumber handles JIDs', () => {
  assert.strictEqual(parsePhoneNumber('919521433306:12@s.whatsapp.net'), '919521433306');
  assert.strictEqual(parsePhoneNumber('15551234567@s.whatsapp.net'), '15551234567');
  assert.strictEqual(parsePhoneNumber(null), null);
  assert.strictEqual(parsePhoneNumber('12@s.whatsapp.net'), null);
});

test('start rejects non-UUID ids before touching anything', async () => {
  const { mgr, deps } = managerWithTmp();
  await assert.rejects(() => mgr.start('../../etc'), (e) => e instanceof InvalidConnectionId);
  await assert.rejects(() => mgr.start('not-a-uuid'), (e) => e instanceof InvalidConnectionId);
  assert.strictEqual(deps.created.length, 0);
});

test('start emits QR, refresh replaces it, freshQr expiry works', async () => {
  let now = 1_000_000;
  const { mgr, deps } = managerWithTmp({ now: () => now });
  const snap = await mgr.start(ID_A);
  assert.strictEqual(snap.connectionId, ID_A);
  assert.strictEqual(deps.created.length, 1);
  // Auth state isolated under <authDir>/<id>.
  assert.ok(deps.dirs[0].endsWith(ID_A));

  const sock = deps.created[0];
  await sock.hooks.onQr('QR-ONE');
  let qr = mgr.freshQr(ID_A);
  assert.ok(qr && qr.qrDataUrl.startsWith('data:image/png'));
  const first = qr.qrDataUrl;

  await sock.hooks.onQr('QR-TWO');
  qr = mgr.freshQr(ID_A);
  assert.ok(qr && qr.qrDataUrl !== first);

  now += 60 * 1000; // past 55s freshness
  assert.strictEqual(mgr.freshQr(ID_A), null);
});

test('open sets connected + identity; second start is idempotent', async () => {
  const { mgr, deps } = managerWithTmp();
  await mgr.start(ID_A);
  const sock = deps.created[0];
  await sock.hooks.onOpen({ id: '919521433306:12@s.whatsapp.net', name: 'Test Biz' });
  const snap = mgr.status(ID_A);
  assert.strictEqual(snap.status, 'connected');
  assert.strictEqual(snap.phoneNumber, '919521433306');
  assert.strictEqual(snap.displayName, 'Test Biz');
  assert.ok(snap.lastSeenAt);

  await mgr.start(ID_A);
  assert.strictEqual(deps.created.length, 1); // no duplicate socket
});

test('logged-out close wipes auth and terminals the session', async () => {
  const { mgr, deps, authDir } = managerWithTmp();
  await fs.mkdir(path.join(authDir, ID_A), { recursive: true });
  await fs.writeFile(path.join(authDir, ID_A, 'creds.json'), '{}');
  await mgr.start(ID_A);
  const sock = deps.created[0];
  await sock.hooks.onClose({ loggedOut: true, statusCode: 401 });
  assert.strictEqual(mgr.status(ID_A).status, 'logged_out');
  let exists = true;
  try {
    await fs.stat(path.join(authDir, ID_A));
  } catch {
    exists = false;
  }
  assert.strictEqual(exists, false);
});

test('non-logout close triggers exactly one reconnect attempt', async () => {
  const { mgr, deps } = managerWithTmp();
  await mgr.start(ID_A);
  const sock = deps.created[0];
  await sock.hooks.onClose({ loggedOut: false, statusCode: 500 });
  assert.strictEqual(mgr.status(ID_A).status, 'reconnecting');
  // Backoff is ~2s + <1s jitter: poll up to 8s, then assert exactly one retry.
  const deadline = Date.now() + 8000;
  while (deps.created.length < 2 && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 100));
  }
  assert.strictEqual(deps.created.length, 2);
});

test('logout ends socket, wipes auth, unknown id still 200 logged_out', async () => {
  const { mgr, deps } = managerWithTmp();
  await mgr.start(ID_A);
  const sock = deps.created[0];
  const snap = await mgr.logout(ID_A);
  assert.strictEqual(snap.status, 'logged_out');
  assert.strictEqual(sock.logoutCalls, 1);
  assert.strictEqual(sock.endCalls, 1);
  const again = await mgr.logout('22222222-2222-4222-8222-222222222222');
  assert.strictEqual(again.status, 'logged_out');
});

test('sessions are isolated per connection id', async () => {
  const { mgr, deps } = managerWithTmp();
  await mgr.start(ID_A);
  await mgr.start(ID_B);
  assert.strictEqual(deps.created.length, 2);
  await deps.created[0].hooks.onOpen({ id: '111@s.whatsapp.net', name: 'A' });
  assert.strictEqual(mgr.status(ID_A).status, 'connected');
  assert.strictEqual(mgr.status(ID_B).status, 'starting');
});

test('HTTP routes enforce key, validate id, serve QR lifecycle', async () => {
  process.env.WA_GATEWAY_SERVICE_KEY = 'test-key-p2';
  const { mgr, deps } = managerWithTmp();
  const app = createApp({
    makeSocket: deps.makeSocket,
    useAuthState: deps.useAuthState,
    authDir: path.join(os.tmpdir(), `wa-test-http-${Date.now()}`),
  });
  const server = await new Promise((resolve) => {
    const s = app.listen(0, '127.0.0.1', () => resolve(s));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    const noAuth = await fetch(`${base}/sessions/${ID_A}/status`);
    assert.strictEqual(noAuth.status, 401);

    const hdr = { 'X-Gateway-Key': 'test-key-p2', 'Content-Type': 'application/json' };
    const bad = await fetch(`${base}/sessions/nope/start`, { method: 'POST', headers: hdr });
    assert.strictEqual(bad.status, 400);

    const started = await fetch(`${base}/sessions/${ID_A}/start`, { method: 'POST', headers: hdr });
    assert.strictEqual(started.status, 202);

    const emptyQr = await fetch(`${base}/sessions/${ID_A}/qr`, { headers: hdr });
    assert.strictEqual(emptyQr.status, 404);
    assert.strictEqual((await emptyQr.json()).detail, 'NO_FRESH_QR');

    await deps.created[0].hooks.onQr('QR-HTTP');
    const qr = await fetch(`${base}/sessions/${ID_A}/qr`, { headers: hdr });
    assert.strictEqual(qr.status, 200);
    assert.ok((await qr.json()).qrDataUrl.startsWith('data:image/png'));

    const unknown = await fetch(`${base}/sessions/${ID_B}/status`, { headers: hdr });
    assert.strictEqual(unknown.status, 404);

    const out = await fetch(`${base}/sessions/${ID_A}`, { method: 'DELETE', headers: hdr });
    assert.strictEqual(out.status, 200);
    assert.strictEqual((await out.json()).status, 'logged_out');

    const re = await fetch(`${base}/sessions/${ID_B}/reconnect`, { method: 'POST', headers: hdr });
    assert.strictEqual(re.status, 404); // unknown session
  } finally {
    server.close();
    delete process.env.WA_GATEWAY_SERVICE_KEY;
  }
});
