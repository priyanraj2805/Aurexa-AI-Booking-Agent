from typing import Annotated, Any, Dict, List, Optional, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class BookingAgentState(TypedDict):
    """The complete state for the LangGraph agent."""

    messages: Annotated[List[BaseMessage], add_messages]
    summary: str  # 1.1 Running summary for token optimization
    session_id: str
    org_id: str
    org_config: Dict[str, Any]
    collected_fields: Dict[str, Any]
    user_timezone: Optional[str]
    optional_skips: List[str]
    pending_conflict: Dict[str, List[Any]]
    follow_up_fields: List[str]
    captured_deltas: Dict[str, Any]
    merge_diagnostics: Dict[str, Any]
    current_intent: str
    otp_verified: bool
    phone_otp_verified: bool
    otp_required: bool
    otp_pending: bool
    otp_event_key: str
    otp_code: str
    validation_errors: Dict[str, str]
    validated_field_metadata: Dict[str, Dict[str, str]]
    knowledge_answer: bool
    context_window: List[Dict[str, str]]
    context_window_size: int
    context_compacted: bool
    date_reset_required: bool
    fallback_reason: str
    fallback_next_step: str
    schedule_answer: Dict[str, Any]
    slot_unavailable: bool
    refreshed_slots: List[str]
