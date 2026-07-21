from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso_utc(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass
class RecoveryResult:
    session_id: str
    state: Dict[str, Any]
    policy: str
    prompt_metadata: Optional[Dict[str, Any]]
    merge_metadata: Dict[str, Any]
    checkpoint_ts: Optional[str]


class SessionRecoveryManager:
    def __init__(
        self,
        get_checkpoint_by_session: Callable[[str], Awaitable[Optional[Tuple[str, Dict[str, Any]]]]],
        get_checkpoint_by_channel: Callable[
            [str, str, str], Awaitable[Optional[Tuple[str, Dict[str, Any]]]]
        ],
        now: Callable[[], datetime] = _utc_now,
        stale_minutes: int = 15,
    ) -> None:
        self._get_checkpoint_by_session = get_checkpoint_by_session
        self._get_checkpoint_by_channel = get_checkpoint_by_channel
        self._now = now
        self._stale_minutes = stale_minutes

    async def recover(
        self,
        *,
        channel: str,
        session_id: Optional[str] = None,
        org_id: Optional[str] = None,
        channel_user_id: Optional[str] = None,
        memory_state: Optional[Dict[str, Any]] = None,
    ) -> Optional[RecoveryResult]:
        checkpoint_record = await self._lookup_checkpoint(
            channel=channel,
            session_id=session_id,
            org_id=org_id,
            channel_user_id=channel_user_id,
        )
        if not checkpoint_record:
            return None

        recovered_session_id, checkpoint = checkpoint_record
        checkpoint_ts = _parse_iso_utc(checkpoint.get("checkpoint_ts"))
        checkpoint_state = dict(checkpoint.get("state") or {})
        merged_state, merge_metadata = self._merge_with_memory(
            checkpoint_state=checkpoint_state,
            checkpoint_ts=checkpoint_ts,
            memory_state=memory_state,
        )
        policy, prompt_metadata = self._policy_for_timestamp(checkpoint_ts, merged_state)

        return RecoveryResult(
            session_id=recovered_session_id,
            state=merged_state,
            policy=policy,
            prompt_metadata=prompt_metadata,
            merge_metadata=merge_metadata,
            checkpoint_ts=checkpoint.get("checkpoint_ts"),
        )

    async def _lookup_checkpoint(
        self,
        *,
        channel: str,
        session_id: Optional[str],
        org_id: Optional[str],
        channel_user_id: Optional[str],
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        if channel == "web":
            if not session_id:
                return None
            return await self._get_checkpoint_by_session(session_id)

        if not org_id or not channel_user_id:
            return None
        return await self._get_checkpoint_by_channel(org_id, channel, channel_user_id)

    def _merge_with_memory(
        self,
        *,
        checkpoint_state: Dict[str, Any],
        checkpoint_ts: Optional[datetime],
        memory_state: Optional[Dict[str, Any]],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not memory_state:
            return checkpoint_state, {"winner": "checkpoint", "conflicting_fields": []}

        memory_ts = _parse_iso_utc(memory_state.get("memory_updated_at"))
        checkpoint_newer = memory_ts is None or (
            checkpoint_ts is not None and checkpoint_ts >= memory_ts
        )
        winner = "checkpoint" if checkpoint_newer else "memory"

        merged = dict(memory_state)
        merged.update(checkpoint_state)

        checkpoint_collected = checkpoint_state.get("collected_data", {})
        memory_collected = memory_state.get("collected_data", {})
        if not isinstance(checkpoint_collected, dict):
            checkpoint_collected = {}
        if not isinstance(memory_collected, dict):
            memory_collected = {}

        conflict_fields = sorted(
            key
            for key in checkpoint_collected
            if key in memory_collected and checkpoint_collected[key] != memory_collected[key]
        )

        if winner == "checkpoint":
            merged_collected = dict(memory_collected)
            merged_collected.update(checkpoint_collected)
        else:
            merged_collected = dict(checkpoint_collected)
            merged_collected.update(memory_collected)
        merged["collected_data"] = merged_collected

        return merged, {"winner": winner, "conflicting_fields": conflict_fields}

    def _policy_for_timestamp(
        self,
        checkpoint_ts: Optional[datetime],
        state: Dict[str, Any],
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        if checkpoint_ts is None:
            return "auto_continue", None

        age_minutes = (self._now() - checkpoint_ts).total_seconds() / 60
        if age_minutes <= self._stale_minutes:
            return "auto_continue", None

        return (
            "resume_or_start_new",
            {
                "default_action": "start_new",
                "last_intent": state.get("current_intent"),
            },
        )
