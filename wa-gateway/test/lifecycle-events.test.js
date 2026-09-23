// Lifecycle event push tests: spontaneous logout/connect notify the backend.
const assert = require('node:assert');
const test = require('node:test');

const { createSessionManager } = require('../src/sessionManager');

const ID_A = '11111111-2222-4333-8444-555555555555';

function makeManager(events, hooksOut) {
  return createSessionManager({
    makeSocket: async ({ hooks }) => {
      if (hooksOut) hooksOut.hooks = hooks;
      return {
        async logout() {},
        async end() {},
        async sendRaw() { return { key: { id: 'S1' } }; },
        async presence() {},
      };
    },
    useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
    authDir: '/tmp/wa-events-test',
    outbox: null,
    deliverInbound: async () => null,
    notifyEvent: async (id, event) => { events.push({ id, event }); },
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
}

test('spontaneous logout fires logged_out event and wipes auth', async () => {
  const events = [];
  const hooksOut = {};
  const mgr = makeManager(events, hooksOut);
  await mgr.start(ID_A);
  await hooksOut.hooks.onClose({ loggedOut: true });
  assert.deepStrictEqual(events, [{ id: ID_A, event: { type: 'logged_out' } }]);
  assert.strictEqual(mgr.status(ID_A).status, 'logged_out');
});

test('connect fires connected event', async () => {
  const events = [];
  const hooksOut = {};
  const mgr = makeManager(events, hooksOut);
  await mgr.start(ID_A);
  await hooksOut.hooks.onOpen({ id: '123@s.whatsapp.net', name: 'Biz' });
  assert.deepStrictEqual(events, [{ id: ID_A, event: { type: 'connected' } }]);
  assert.strictEqual(mgr.status(ID_A).status, 'connected');
});

test('transient close fires no event and reconnects', async () => {
  const events = [];
  const hooksOut = {};
  const mgr = makeManager(events, hooksOut);
  await mgr.start(ID_A);
  await hooksOut.hooks.onClose({ loggedOut: false });
  assert.deepStrictEqual(events, []);
});

test('notify failures never break socket handling', async () => {
  const hooksOut = {};
  const mgr = createSessionManager({
    makeSocket: async ({ hooks }) => {
      hooksOut.hooks = hooks;
      return {
        async logout() {},
        async end() {},
        async sendRaw() { return {}; },
        async presence() {},
      };
    },
    useAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
    authDir: '/tmp/wa-events-fail-test',
    outbox: null,
    deliverInbound: async () => null,
    notifyEvent: async () => { throw new Error('backend down'); },
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  await mgr.start(ID_A);
  await hooksOut.hooks.onOpen({ id: '1@s.whatsapp.net', name: null });
  await hooksOut.hooks.onClose({ loggedOut: true });
  assert.strictEqual(mgr.status(ID_A).status, 'logged_out');
});
