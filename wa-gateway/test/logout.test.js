// Logout ordering regression test: the remove-companion-device stanza must
// be sent BEFORE the socket closes, otherwise Linked Devices keeps a ghost.
const assert = require('node:assert');
const test = require('node:test');

const { logoutSocket } = require('../src/baileys');

test('logout sends remove-companion-device, waits, then ends socket', async () => {
  const calls = [];
  let waited = 0;
  const fakeSock = {
    async logout() { calls.push('logout'); },
    async end() { calls.push('end'); },
  };
  await logoutSocket(fakeSock, 5, async (ms) => { waited = ms; });
  assert.deepStrictEqual(calls, ['logout', 'end']);
  assert.strictEqual(waited, 5);
});

test('logout still ends socket when logout throws (already closed)', async () => {
  const calls = [];
  const fakeSock = {
    async logout() { calls.push('logout'); throw new Error('closed'); },
    async end() { calls.push('end'); },
  };
  await logoutSocket(fakeSock, 1, async () => {});
  assert.deepStrictEqual(calls, ['logout', 'end']);
});
