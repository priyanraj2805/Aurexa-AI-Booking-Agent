// P4 tests: outbox queue, backoff, watchdog, forceReconnect, touch.
const assert = require('node:assert');
const test = require('node:test');

const { createOutbox } = require('../src/outbox');
const { createWatchdog } = require('../src/watchdog');
const { createSessionManager, backoffDelayMs } = require('../src/sessionManager');

const ID_A = '11111111-2222-4333-8444-555555555555';

function fastOutbox(over) {
  const slept = [];
  return {
    slept,
    box: createOutbox({
      minGapMs: 5,
      maxPerMinute: 1000,
      baseBackoffMs: 5,
      dedupeTtlMs: 60 * 1000,
      now: () => Date.now(),
      delay: async (ms) => { slept.push(ms); },
      logger: { info: () => {}, warn: () => {}, error: () => {} },
      ...(over || {}),
    }),
  };
}

test('backoffDelayMs grows exponentially within cap', () => {
  const d0 = backoffDelayMs(0);
  assert.ok(d0 >= 2000 && d0 < 3000, `d0=${d0}`);
  const d3 = backoffDelayMs(3);
  assert.ok(d3 >= 16000 && d3 < 17000, `d3=${d3}`);
  const dBig = backoffDelayMs(100);
  assert.ok(dBig >= 300000 && dBig < 301000, `dBig=${dBig}`);
  // Pairing cap (QR screen): pairing sessions retry fast, never minutes.
  const pBig = backoffDelayMs(100, 20 * 1000);
  assert.ok(pBig >= 20000 && pBig < 21000, `pBig=${pBig}`);
});

test('outbox sends FIFO with dedupe keys', async () => {
  const { box } = fastOutbox();
  const order = [];
  const p1 = box.enqueue(ID_A, 'k1', async () => { order.push(1); return 'r1'; });
  const p2 = box.enqueue(ID_A, 'k1', async () => { order.push(2); return 'r2'; });
  const p3 = box.enqueue(ID_A, null, async () => { order.push(3); return 'r3'; });
  const [r1, r2, r3] = await Promise.all([p1, p2, p3]);
  assert.deepStrictEqual(order, [1, 3]); // duplicate k1 never ran
  assert.strictEqual(r2.deduped, true);
  assert.strictEqual(r1.result, 'r1');
  assert.strictEqual(r3.attempts, 1);
});

test('outbox retries with backoff then drops', async () => {
  const { box, slept } = fastOutbox();
  let calls = 0;
  await assert.rejects(
    box.enqueue(ID_A, null, async () => {
      calls += 1;
      throw new Error(`boom-${calls}`);
    }),
    /boom-3/
  );
  assert.strictEqual(calls, 3); // initial + 2 retries (maxAttempts=3)
  assert.ok(slept.length >= 2);
  assert.ok(slept[0] >= 5 && slept[0] < 50);
});

test('outbox enforces per-minute ceiling instead of bursting', async () => {
  const waited = [];
  const { box } = fastOutbox({
    maxPerMinute: 2,
    delay: async (ms) => { waited.push(ms); },
  });
  const done = [];
  await Promise.all([
    box.enqueue(ID_A, 'a', async () => { done.push('a'); }),
    box.enqueue(ID_A, 'b', async () => { done.push('b'); }),
    box.enqueue(ID_A, 'c', async () => { done.push('c'); }),
  ]);
  assert.deepStrictEqual(done, ['a', 'b', 'c']);
  assert.ok(waited.some((ms) => ms > 1000), 'third send waited for window');
});

test('outbox isolates connections from each other', async () => {
  const { box } = fastOutbox();
  const other = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';
  let failCalls = 0;
  const bad = box.enqueue(ID_A, null, async () => { failCalls += 1; throw new Error('x'); }).catch(() => 'dropped');
  const good = await box.enqueue(other, null, async () => 'fine');
  assert.strictEqual(good.result, 'fine');
  assert.strictEqual(await bad, 'dropped');
  assert.strictEqual(failCalls, 3);
});

test('outbox shutdown stops accepting and drains', async () => {
  const { box } = fastOutbox();
  await box.enqueue(ID_A, null, async () => 'ok');
  await box.shutdown(1000);
  await assert.rejects(box.enqueue(ID_A, null, async () => 'x'), /OUTBOX_SHUTDOWN/);
});

test('watchdog restarts silent sessions, spares active and logged-out ones', async () => {
  let now = 1_000_000;
  const restarted = [];
  const sessions = [
    { connectionId: 'silent-1', status: 'reconnecting', lastSeenAt: new Date(now - 20 * 60 * 1000).toISOString() },
    { connectionId: 'active-1', status: 'connected', lastSeenAt: new Date(now - 60 * 1000).toISOString() },
    { connectionId: 'connected-idle-1', status: 'connected', lastSeenAt: new Date(now - 70 * 60 * 1000).toISOString() },
    { connectionId: 'dead-1', status: 'logged_out', lastSeenAt: new Date(0).toISOString() },
    { connectionId: 'fresh-1', status: 'awaiting_qr', lastSeenAt: new Date(0).toISOString() },
  ];
  const fakeManager = {
    list: () => sessions,
    forceReconnect: async (id) => { restarted.push(id); },
  };
  const wd = createWatchdog({
    manager: fakeManager,
    silentMs: 10 * 60 * 1000,
    now: () => now,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  const out = await wd.tick();
  assert.deepStrictEqual(out, ['silent-1']);
  assert.deepStrictEqual(restarted, ['silent-1']);
});

function fakeManager() {
  const created = [];
  const mgr = createSessionManager({
    makeSocket: async ({ hooks }) => {
      const h = { hooks, async logout() {}, async end() {} };
      created.push(h);
      return h;
    },
    useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
    authDir: '/tmp/wa-reliability',
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  return { mgr, created };
}

test('forceReconnect restarts live session without wiping; unknown returns null', async () => {
  const { mgr, created } = fakeManager();
  assert.strictEqual(await mgr.forceReconnect(ID_A), null);
  await mgr.start(ID_A);
  await created[0].hooks.onOpen({ id: '15551234567@s.whatsapp.net', name: 'Shop' });
  const before = created.length;
  const snap = await mgr.forceReconnect(ID_A);
  assert.strictEqual(created.length, before + 1);
  assert.strictEqual(snap.status, 'reconnecting');
  // Logged-out sessions are left alone (need re-scan, not restart).
  await created[0].hooks.onClose({ loggedOut: true });
  const snap2 = await mgr.forceReconnect(ID_A);
  assert.strictEqual(snap2.status, 'logged_out');
  assert.strictEqual(created.length, before + 1);
});

test('touch refreshes lastSeenAt (P7 message-activity seam)', async () => {
  let now = 5_000_000;
  const { mgr, created } = (function make() {
    const created = [];
    const mgr = createSessionManager({
      makeSocket: async ({ hooks }) => {
        const h = { hooks, async logout() {}, async end() {} };
        created.push(h);
        return h;
      },
      useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
      authDir: '/tmp/wa-reliability',
      now: () => now,
      logger: { info: () => {}, warn: () => {}, error: () => {} },
    });
    return { mgr, created };
  })();
  await mgr.start(ID_A);
  await created[0].hooks.onOpen({ id: '15551234567@s.whatsapp.net', name: null });
  assert.strictEqual(mgr.status(ID_A).lastSeenAt, new Date(5_000_000).toISOString());
  now += 42_000;
  mgr.touch(ID_A);
  assert.strictEqual(mgr.status(ID_A).lastSeenAt, new Date(5_042_000).toISOString());
});
