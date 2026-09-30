"""
WhatsApp message debouncer and sequencer.

Handles out-of-order and duplicate inbound messages:
- Deduplicates by tracking processed message IDs
- Uses asyncio locks to prevent concurrent processing of same session
- Cleans up old locks to prevent memory leaks
"""

import asyncio
import logging
import os
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# In-memory storage for deduplication (use Redis for production/multi-worker)
_processed_message_ids: Dict[str, float] = {}

# Locks per session to ensure sequential processing
_session_locks: Dict[str, asyncio.Lock] = {}

# Lock timestamps for cleanup
_lock_timestamps: Dict[str, float] = {}

# Cleanup interval (in seconds)
CLEANUP_INTERVAL = 300  # 5 minutes
MESSAGE_ID_TTL = 300  # 5 minutes
LOCK_TTL = 3600  # 1 hour

# Flood protection: sliding-window per-sender counter. A plain TTL cache
# rewrites its expiry on EVERY hit, so any sustained conversation (replies
# every <60s) trips it and then stays tripped — the customer is silenced
# with zero feedback. A sliding log only counts messages inside the last
# 60 seconds, so normal pacing can chat forever and only true bursts pause.
RATE_LIMIT_MAX_MESSAGES = 10
RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_MAX_SENDERS = 10000

# sender key -> ascending list of hit timestamps (oldest first)
_sender_message_times: Dict[str, list] = {}

_processed_task: Optional[asyncio.Task] = None


def _get_session_lock(session_id: str) -> asyncio.Lock:
    """Get or create a lock for a specific session."""
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    _lock_timestamps[session_id] = time.time()
    return _session_locks[session_id]


async def _cleanup_loop():
    """Background task to clean up old processed message IDs and stale locks."""
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            now = time.time()

            # Clean up expired message IDs
            expired_msg_ids = [
                k for k, v in _processed_message_ids.items() if now - v > MESSAGE_ID_TTL
            ]
            for k in expired_msg_ids:
                del _processed_message_ids[k]
            if expired_msg_ids:
                logger.debug(
                    f"Cleaned up {len(expired_msg_ids)} expired WhatsApp message IDs"
                )

            # Clean up expired rate-limit windows
            try:
                pruned = prune_rate_limit_state(now=now)
                if pruned:
                    logger.debug(f"Cleaned up {pruned} expired WhatsApp rate-limit windows")
            except Exception:
                pass

            # Clean up stale session locks
            stale_locks = [k for k, v in _lock_timestamps.items() if now - v > LOCK_TTL]
            for k in stale_locks:
                if k in _session_locks:
                    lock = _session_locks[k]
                    if not lock.locked():
                        del _session_locks[k]
                        del _lock_timestamps[k]
                else:
                    del _lock_timestamps[k]
            if stale_locks:
                logger.debug(
                    f"Cleaned up {len(stale_locks)} stale WhatsApp session locks"
                )

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Cleanup loop error: {e}")


def start_debouncer():
    """Start the background cleanup task."""
    global _processed_task
    if _processed_task is None:
        _processed_task = asyncio.create_task(_cleanup_loop())


def stop_debouncer():
    """Stop the background cleanup task."""
    global _processed_task
    if _processed_task is not None:
        _processed_task.cancel()
        _processed_task = None


def is_duplicate(message_id: str) -> bool:
    """
    Check if a message ID has already been processed.

    Returns True if duplicate (should skip), False if new message.
    """
    if message_id in _processed_message_ids:
        logger.info(f"Dropping duplicate WhatsApp message: {message_id}")
        return True

    _processed_message_ids[message_id] = time.time()
    return False


def release_message(message_id: str) -> None:
    _processed_message_ids.pop(message_id, None)


def _prune_sender_hits(hits: list, cutoff: float) -> None:
    """Drop timestamps outside the window, in place (lists stay tiny)."""
    while hits and hits[0] <= cutoff:
        hits.pop(0)


def check_sender_rate_limit(
    sender_key: str, *, now: Optional[float] = None
) -> tuple:
    """Sliding-window flood check for one sender.

    Returns (limited, retry_after_seconds). Counts only messages inside the
    last RATE_LIMIT_WINDOW_SECONDS, so steady conversations never trip it —
    only true bursts pause, and callers must answer those with a slowdown
    notice, never silence.
    """
    ts = now if now is not None else time.time()
    hits = _sender_message_times.get(sender_key)
    if hits is None:
        if len(_sender_message_times) >= _RATE_MAX_SENDERS:
            # Best-effort cap: evict the oldest-inserted sender.
            _sender_message_times.pop(next(iter(_sender_message_times)), None)
        hits = []
        _sender_message_times[sender_key] = hits
    _prune_sender_hits(hits, ts - RATE_LIMIT_WINDOW_SECONDS)
    if len(hits) >= RATE_LIMIT_MAX_MESSAGES:
        retry_after = hits[0] + RATE_LIMIT_WINDOW_SECONDS - ts
        return True, max(0.0, retry_after)
    hits.append(ts)
    return False, 0.0


def prune_rate_limit_state(*, now: Optional[float] = None) -> int:
    """Drop fully-expired sender windows. Returns entries removed."""
    ts = now if now is not None else time.time()
    cutoff = ts - RATE_LIMIT_WINDOW_SECONDS
    stale = [
        key
        for key, hits in _sender_message_times.items()
        if not hits or hits[-1] <= cutoff
    ]
    for key in stale:
        _sender_message_times.pop(key, None)
    return len(stale)


def get_session_lock(session_id: str) -> asyncio.Lock:
    """Get the lock for a specific session."""
    return _get_session_lock(session_lock_key(session_id))


# Transport prefixes that used to be part of the lock key. The same human can
# reach us over both QR/Baileys and Meta Cloud, and those handlers used
# different key shapes (`<session_id>` vs `cloud:<session_id>`), so one
# conversation could be processed concurrently by two locks. Locking is
# per-conversation, so the key must be derived canonically in ONE place.
_TRANSPORT_LOCK_PREFIXES = ("qr:", "cloud:", "meta:", "whatsapp_cloud:")


def session_lock_key(session_id: str) -> str:
    """Canonical lock key for a conversation, independent of transport.

    Both inbound transports must pass through here so a session can never end
    up with two independent locks.
    """
    key = str(session_id or "").strip()
    changed = True
    while changed:
        changed = False
        for prefix in _TRANSPORT_LOCK_PREFIXES:
            if key.startswith(prefix):
                key = key[len(prefix):]
                changed = True
    return key or "__unknown_session__"


# ── Multi-process safety ───────────────────────────────────────────────────
# Everything above this section is plain module-level dicts, so it is scoped to
# ONE OS process. The deployment is currently single-process (uvicorn is
# started without `workers=`), which is why this is correct today.
#
# Classification of the process-local state:
#
#   A. Must be distributed if this ever runs multi-process
#      - message deduplication: a Meta retry (or a Baileys redelivery) landing
#        on a second worker is not recognised, so the turn runs twice.
#      - per-conversation session lock: two messages from one customer can be
#        processed concurrently on two workers, and collected_data is a
#        read-modify-write JSONB merge, so one turn's fields can overwrite the
#        other's.
#      - flood window: the effective limit becomes N x RATE_LIMIT_MAX_MESSAGES.
#
#   B. Safe to leave process-local
#      - _lock_timestamps / _processed_task / the cleanup loop: bookkeeping for
#        the local maps only.
#      - the in-process LangGraph MemorySaver: not used for state. Messages and
#        the durable checkpoint are rebuilt from Postgres every turn
#        (chat_sessions.state_data["runtime_checkpoint"]).
#
#   C. Already protected by PostgreSQL -- NOT replaced by anything above
#      - duplicate bookings: attempt_booking takes
#        `SELECT ... FOR UPDATE NOWAIT` on the slot, checks is_booked, and
#        bookings.slot_id is UNIQUE.
#      - double OTP verification: otp_records is UNIQUE(session_id, channel)
#        and verify_otp locks the row NOWAIT.
#      - session status races: atomic_set_confirming does ACTIVE -> CONFIRMING
#        under NOWAIT.
#
# Nothing here is moved to Redis. Redis is declared in docker-compose but has no
# client code at all, so adopting it would mean adding a dependency and a new
# failure mode to the booking path, and the test suite runs on SQLite and
# cannot verify Postgres advisory-lock semantics.
#
# Instead the risk is made LOUD at startup, so a multi-process deployment
# cannot silently inherit weaker guarantees than single-process.
_PROCESS_LOCAL_MECHANISMS = (
    "WhatsApp message deduplication (_processed_message_ids)",
    "per-conversation session lock (_session_locks)",
    "per-sender flood window (_sender_message_times)",
)


def detect_configured_worker_count() -> int:
    """Worker count the process was configured with. 1 when unknown/single."""
    for var in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "APP_WORKERS"):
        raw = os.environ.get(var)
        if raw and raw.strip().isdigit():
            try:
                count = int(raw.strip())
            except ValueError:
                continue
            if count > 0:
                return count
    return 1


def multi_process_risk_report(
    *, worker_count: int, acknowledged: bool = False
) -> Optional[str]:
    """Describe the process-local mechanisms that weaken with >1 worker.

    Returns None when the deployment is single-process (or the operator has
    explicitly acknowledged the trade-off), otherwise a single log-ready
    string naming every affected mechanism.
    """
    if worker_count <= 1 or acknowledged:
        return None
    listed = "\n".join(f"    - {item}" for item in _PROCESS_LOCAL_MECHANISMS)
    return (
        f"WhatsApp inbound is running with {worker_count} workers. The following "
        "are per-process in-memory only and will NOT be shared between "
        f"workers:\n{listed}\n"
        "Duplicate-booking safety is unaffected: it is enforced in PostgreSQL "
        "(slot row lock + UNIQUE(bookings.slot_id)). Set "
        "WHATSAPP_MULTI_PROCESS_ACK=true to acknowledge this, or run a single "
        "worker."
    )
