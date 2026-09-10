// Per-connection outbound send queue — the conservative-messaging core.
//
// Guarantees, per connection id:
// - FIFO serial sends with a minimum gap (default 1200ms) between sends.
// - Per-minute ceiling (default 20); overflow waits instead of bursting.
// - Retries with exponential backoff (default 3 attempts).
// - Duplicate-send prevention: caller idempotency keys are remembered for a
//   TTL, so a retried backend call never double-sends.
//
// All timings injectable for fast deterministic tests.
const DEFAULT_MIN_GAP_MS = 1200;
const DEFAULT_MAX_PER_MINUTE = 20;
const DEFAULT_MAX_ATTEMPTS = 3;
const DEFAULT_BASE_BACKOFF_MS = 1000;
const DEFAULT_DEDUPE_TTL_MS = 10 * 60 * 1000;

function createOutbox(options) {
  const o = options || {};
  const minGapMs = o.minGapMs ?? DEFAULT_MIN_GAP_MS;
  const maxPerMinute = o.maxPerMinute ?? DEFAULT_MAX_PER_MINUTE;
  const maxAttempts = o.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  const baseBackoffMs = o.baseBackoffMs ?? DEFAULT_BASE_BACKOFF_MS;
  const dedupeTtlMs = o.dedupeTtlMs ?? DEFAULT_DEDUPE_TTL_MS;
  const now = o.now || (() => Date.now());
  const delay = o.delay || ((ms) => new Promise((r) => setTimeout(r, ms)));
  const log = o.logger || { info: () => {}, warn: () => {}, error: () => {} };

  const queues = new Map(); // id -> [{ key, task, resolve, reject, attempts }]
  const pumping = new Map(); // id -> bool
  const seen = new Map(); // idempotencyKey -> expiresAt (completed sends)
  const inflight = new Set(); // keys currently queued or sending
  const sentAt = new Map(); // id -> [timestamps ms]
  const lastSend = new Map(); // id -> timestamp ms
  let accepting = true;

  function pruneSeen() {
    const t = now();
    for (const [k, exp] of seen) {
      if (exp <= t) seen.delete(k);
    }
  }

  function enqueue(connectionId, key, task) {
    if (!accepting) return Promise.reject(new Error('OUTBOX_SHUTDOWN'));
    pruneSeen();
    if (key && (seen.has(key) || inflight.has(key))) {
      return Promise.resolve({ deduped: true });
    }
    return new Promise((resolve, reject) => {
      if (key) inflight.add(key);
      if (!queues.has(connectionId)) queues.set(connectionId, []);
      queues.get(connectionId).push({ key, task, resolve, reject, attempts: 0 });
      void pump(connectionId);
    });
  }

  async function waitForRateWindow(id) {
    for (;;) {
      const t = now();
      const marks = (sentAt.get(id) || []).filter((m) => m > t - 60 * 1000);
      sentAt.set(id, marks);
      if (marks.length < maxPerMinute) {
        const gap = minGapMs - (t - (lastSend.get(id) ?? 0));
        if (gap > 0) await delay(gap);
        return;
      }
      const waitMs = Math.max(0, marks[0] + 60 * 1000 - t);
      log.warn(`outbox throttled for session (per-minute ceiling)`);
      await delay(waitMs);
    }
  }

  async function pump(id) {
    if (pumping.get(id)) return;
    pumping.set(id, true);
    try {
      for (;;) {
        const q = queues.get(id);
        const item = q && q.length > 0 ? q[0] : null;
        if (!item) return;
        await waitForRateWindow(id);
        item.attempts += 1;
        try {
          const result = await item.task();
          q.shift();
          sentAt.set(id, [...(sentAt.get(id) || []), now()]);
          lastSend.set(id, now());
          if (item.key) {
            inflight.delete(item.key);
            seen.set(item.key, now() + dedupeTtlMs);
          }
          item.resolve({ deduped: false, result, attempts: item.attempts });
        } catch (err) {
          if (item.attempts >= maxAttempts) {
            q.shift();
            if (item.key) inflight.delete(item.key);
            log.error(`outbox dropped message after ${item.attempts} attempts: ${err.message}`);
            item.reject(err);
          } else {
            const backoff = baseBackoffMs * 2 ** (item.attempts - 1);
            log.warn(`outbox retry ${item.attempts}/${maxAttempts} in ${backoff}ms`);
            await delay(backoff);
          }
        }
      }
    } finally {
      pumping.set(id, false);
    }
  }

  function depth(id) {
    return (queues.get(id) || []).length;
  }

  async function shutdown(timeoutMs) {
    accepting = false;
    const deadline = now() + (timeoutMs ?? 5000);
    for (;;) {
      const busy = [...pumping.values()].some(Boolean);
      const pending = [...queues.values()].some((q) => q.length > 0);
      if ((!busy && !pending) || now() >= deadline) break;
      await delay(50);
    }
  }

  return { enqueue, depth, shutdown };
}

module.exports = {
  createOutbox,
  DEFAULT_MIN_GAP_MS,
  DEFAULT_MAX_PER_MINUTE,
  DEFAULT_MAX_ATTEMPTS,
};
