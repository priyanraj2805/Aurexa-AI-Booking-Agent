import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

HIGH_VALUE_EVENTS = {
    "field_capture",
    "field_edit",
    "field_skip",
    "confirmation",
    "booking_commit",
}

CANONICAL_STATE_KEYS = {
    "collected_data",
    "missing_fields",
    "current_intent",
    "session_status",
    "otp_verified",
    "pending_cancel",
    "summary",
    "context_window",
    "context_window_size",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CheckpointManager:
    def __init__(
        self,
        write_checkpoint: Callable[[str, Dict[str, Any]], Awaitable[None]],
        now: Callable[[], datetime] = _utc_now,
        debounce_seconds: int = 5,
    ) -> None:
        self._write_checkpoint = write_checkpoint
        self._now = now
        self._debounce_seconds = debounce_seconds
        self._session_last_write: dict[str, datetime] = {}
        self._session_last_digest: dict[str, str] = {}

    async def record_event(
        self,
        session_id: str,
        event_type: str,
        state: Dict[str, Any],
    ) -> bool:
        if event_type not in HIGH_VALUE_EVENTS:
            return False

        now = self._now()
        checkpoint = self._build_checkpoint(event_type=event_type, state=state, now=now)
        digest = self._digest_payload(event_type=event_type, state=checkpoint["state"])

        last_write = self._session_last_write.get(session_id)
        last_digest = self._session_last_digest.get(session_id)
        within_debounce = (
            last_write is not None
            and (now - last_write).total_seconds() < self._debounce_seconds
        )
        if within_debounce and digest == last_digest:
            return False

        await self._write_checkpoint(session_id, checkpoint)
        self._session_last_write[session_id] = now
        self._session_last_digest[session_id] = digest
        return True

    def _build_checkpoint(
        self,
        event_type: str,
        state: Dict[str, Any],
        now: datetime,
    ) -> Dict[str, Any]:
        canonical_state = {
            key: state[key] for key in CANONICAL_STATE_KEYS if key in state
        }
        return {
            "checkpoint_version": 1,
            "event_type": event_type,
            "checkpoint_ts": now.astimezone(timezone.utc).isoformat(),
            "state": canonical_state,
        }

    @staticmethod
    def _digest_payload(event_type: str, state: Dict[str, Any]) -> str:
        payload = json.dumps({"event_type": event_type, "state": state}, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
