// P3 tests: persistent auth storage layout, permissions, resume discovery.
const assert = require('node:assert');
const test = require('node:test');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs/promises');

const {
  ensureAuthDir,
  hasCredentials,
  wipeConnection,
  discoverPaired,
} = require('../src/authStore');
const { createSessionManager } = require('../src/sessionManager');

const ID_A = '11111111-2222-4333-8444-555555555555';
const ID_B = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee';

function tmpRoot() {
  return path.join(os.tmpdir(), `wa-auth-test-${Date.now()}-${Math.floor(Math.random() * 1e6)}`);
}

test('ensureAuthDir creates 0700 dirs; hasCredentials tracks creds.json', async () => {
  const root = tmpRoot();
  const dir = await ensureAuthDir(root, ID_A);
  assert.strictEqual(dir, path.join(root, ID_A));
  assert.strictEqual(await hasCredentials(root, ID_A), false);
  await fs.writeFile(path.join(dir, 'creds.json'), '{}');
  assert.strictEqual(await hasCredentials(root, ID_A), true);
  // Wipe removes the whole connection dir.
  await wipeConnection(root, ID_A);
  assert.strictEqual(await hasCredentials(root, ID_A), false);
  await fs.rm(root, { recursive: true, force: true });
});

test('auth dir is owner-only (0700) where the platform supports modes', async () => {
  const root = tmpRoot();
  const dir = await ensureAuthDir(root, ID_A);
  const st = await fs.stat(dir);
  // Windows ACLs ignore POSIX modes; only enforce on POSIX.
  if (process.platform !== 'win32') {
    assert.strictEqual(st.mode & 0o777, 0o700);
  }
  await fs.rm(root, { recursive: true, force: true });
});

test('discoverPaired returns only UUID dirs containing creds.json', async () => {
  const root = tmpRoot();
  await ensureAuthDir(root, ID_A);
  await fs.writeFile(path.join(root, ID_A, 'creds.json'), '{}');
  await ensureAuthDir(root, ID_B); // no creds.json -> not paired
  await fs.mkdir(path.join(root, 'not-a-uuid'));
  await fs.writeFile(path.join(root, 'not-a-uuid', 'creds.json'), '{}');
  await fs.writeFile(path.join(root, 'stray.txt'), 'x');
  assert.deepStrictEqual(await discoverPaired(root), [ID_A]);
  // Missing root resumes to nothing (fresh volume).
  assert.deepStrictEqual(await discoverPaired(path.join(root, 'nope')), []);
  await fs.rm(root, { recursive: true, force: true });
});

test('resume() restarts previously-paired sessions without QR', async () => {
  const root = tmpRoot();
  await ensureAuthDir(root, ID_A);
  await fs.writeFile(path.join(root, ID_A, 'creds.json'), '{}');
  const usedDirs = [];
  const created = [];
  const mgr = createSessionManager({
    makeSocket: async ({ hooks }) => {
      const h = { hooks, async logout() {}, async end() {} };
      created.push(h);
      return h;
    },
    useAuthState: async (dir) => {
      usedDirs.push(dir);
      return { state: {}, saveCreds: async () => {} };
    },
    authDir: root,
    logger: { info: () => {}, warn: () => {}, error: () => {} },
  });
  const started = await mgr.resume();
  assert.deepStrictEqual(started, [ID_A]);
  assert.strictEqual(created.length, 1);
  // Existing creds dir reused as-is (no wipe, no fresh pairing needed).
  assert.ok(usedDirs[0].endsWith(ID_A));
  await fs.rm(root, { recursive: true, force: true });
});
