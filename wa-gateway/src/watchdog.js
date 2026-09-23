// Connection watchdog — OpenClaw two-signal model, conservative edition.
//
// - A quiet-but-connected session is NEVER restarted (no messages is fine).
// - A session with no *transport* activity past the silence budget IS
//   restarted (socket wedged), via manager.forceReconnect().
// - Transport activity = any socket event (QR/open/close) plus, from P7,
//   inbound messages via manager.touch().
// - Logout/dead states are left alone; only RECONNECTING stale is restarted.
//   Connected idle is left alone until Docker down or WA unlink (user request).
const DEFAULT_SILENCE_MS = 60 * 60 * 1000;
const DEFAULT_INTERVAL_MS = 60 * 1000;

function createWatchdog({ manager, silentMs, intervalMs, now, logger, onRestart }) {
  const budget = silentMs ?? DEFAULT_SILENCE_MS;
  const every = intervalMs ?? DEFAULT_INTERVAL_MS;
  const clock = now || (() => Date.now());
  const log = logger || { info: () => {}, warn: () => {}, error: () => {} };
  let timer = null;

  async function tick() {
    let sessions = [];
    try {
      sessions = manager.list();
    } catch (err) {
      log.error(`watchdog list failed: ${err.message}`);
      return [];
    }
    const restarted = [];
    for (const s of sessions) {
      // Only restart stale RECONNECTING, never a healthy connected idle (user request: stay up until WA unlink or Docker down)
      if (s.status !== 'reconnecting') continue;
      const last = s.lastSeenAt ? Date.parse(s.lastSeenAt) : 0;
      if (Number.isNaN(last)) continue;
      if (clock() - last <= budget) continue;
      try {
        await manager.forceReconnect(s.connectionId);
        restarted.push(s.connectionId);
        log.warn(`watchdog restarting silent session`);
        if (onRestart) onRestart(s.connectionId);
      } catch (err) {
        log.error(`watchdog restart failed: ${err.message}`);
      }
    }
    return restarted;
  }

  function start() {
    if (timer) return;
    timer = setInterval(() => {
      tick().catch((err) => log.error(`watchdog tick failed: ${err.message}`));
    }, every);
    if (timer.unref) timer.unref();
  }

  function stop() {
    if (timer) clearInterval(timer);
    timer = null;
  }

  return { tick, start, stop };
}

module.exports = { createWatchdog, DEFAULT_SILENCE_MS, DEFAULT_INTERVAL_MS };
