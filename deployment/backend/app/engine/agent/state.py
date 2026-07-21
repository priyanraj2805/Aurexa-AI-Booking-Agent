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
    otp_required: bool
    otp_event_key: str
    otp_code: str
