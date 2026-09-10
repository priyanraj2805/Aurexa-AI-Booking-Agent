// Persistent Baileys auth-state storage.
//
// Layout: <authDir>/<connectionId>/creds.json (+ Baileys key files), one
// directory per organization connection. Directories are created 0700 and
// re-enforced on every start — these files grant full WhatsApp account
// access and must never be world-readable.
//
// Resume rule: a directory containing creds.json means "previously paired";
// the gateway auto-resumes those sessions on boot (no QR needed) and the
// backend treats them as reconnecting until the socket opens.
const fs = require('node:fs/promises');
const path = require('node:path');

const CREDS_FILE = 'creds.json';
const DIR_MODE = 0o700;

function connectionDir(authDir, id) {
  return path.join(authDir, id);
}

async function ensureAuthDir(authDir, id) {
  const dir = connectionDir(authDir, id);
  await fs.mkdir(dir, { recursive: true, mode: DIR_MODE });
  try {
    await fs.chmod(dir, DIR_MODE);
  } catch (_) {
    // chmod is best-effort on filesystems that ignore modes (some mounts).
  }
  return dir;
}

async function hasCredentials(authDir, id) {
  try {
    const st = await fs.stat(path.join(connectionDir(authDir, id), CREDS_FILE));
    return st.isFile();
  } catch (_) {
    return false;
  }
}

async function wipeConnection(authDir, id) {
  await fs.rm(connectionDir(authDir, id), { recursive: true, force: true });
}

// Ids previously paired = subdirectories containing creds.json. Directory
// names are validated by the caller (sessionManager enforces UUID shape
// before any start); this scan additionally ignores anything unexpected.
async function discoverPaired(authDir) {
  let entries = [];
  try {
    entries = await fs.readdir(authDir, { withFileTypes: true });
  } catch (_) {
    return [];
  }
  const out = [];
  for (const e of entries) {
    if (!e.isDirectory()) continue;
    if (!/^[0-9a-fA-F-]{36}$/.test(e.name)) continue;
    // eslint-disable-next-line no-await-in-loop
    if (await hasCredentials(authDir, e.name)) out.push(e.name);
  }
  return out;
}

module.exports = {
  CREDS_FILE,
  DIR_MODE,
  connectionDir,
  ensureAuthDir,
  hasCredentials,
  wipeConnection,
  discoverPaired,
};
