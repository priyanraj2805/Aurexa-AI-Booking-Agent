"""
WhatsApp webhook message debouncer and sequencer.

Handles out-of-order and duplicate webhook messages from Meta:
- Deduplicates by tracking processed message IDs
- Uses asyncio locks to prevent concurrent processing of same session
- Cleans up old locks to prevent memory leaks
"""

import asyncio
import logging
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


def get_session_lock(session_id: str) -> asyncio.Lock:
    """Get the lock for a specific session."""
    return _get_session_lock(session_id)
