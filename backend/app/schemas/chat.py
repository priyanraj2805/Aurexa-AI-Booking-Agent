from datetime import datetime
import uuid
from pydantic import BaseModel, UUID4, Field, field_validator
from typing import Optional, Dict, Any

class ChatMessageRequest(BaseModel):
    session_id: Optional[UUID4] = None
    message: str = Field(..., min_length=1, max_length=2000)
    # Client-generated message ID for deduplication (optional but recommended)
    message_id: Optional[str] = Field(default=None, max_length=64)
    selected_slot_id: Optional[str] = Field(default=None, max_length=200)
    user_timezone: Optional[str] = Field(default=None, max_length=64)
    timezone_source: Optional[str] = Field(default=None, max_length=20)
    timezone_fallback_used: Optional[bool] = None
    timezone_fallback_reason: Optional[str] = Field(default=None, max_length=64)
    # Org-mode: picked department + doctor (validated, pinned to session)
    unit_id: Optional[str] = Field(default=None, max_length=64)
    provider_id: Optional[str] = Field(default=None, max_length=64)
    # Widget conversation mode: ask (KB Q&A only) | booking (guided booking
    # only). Pinned to collected_data["_mode"]; None = legacy mixed flow.
    mode: Optional[str] = Field(default=None, max_length=16)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        if normalized not in ("ask", "booking"):
            raise ValueError("mode must be one of ask, booking")
        return normalized

    @field_validator("message")
    @classmethod
    def reject_whitespace_only(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Message cannot be empty or whitespace only.")
        return v

    @field_validator("selected_slot_id")
    @classmethod
    def validate_selected_slot_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            return None

        if normalized.startswith("virtual|"):
            parts = normalized.split("|")
            # virtual|<org>|<start>  OR  virtual|<org>|<provider>|<start>
            if len(parts) not in (3, 4):
                raise ValueError(
                    "selected_slot_id virtual format must be virtual|<org_id>|<start_utc> "
                    "or virtual|<org_id>|<provider_id>|<start_utc>"
                )
            try:
                uuid.UUID(parts[1])
                start_part = parts[2] if len(parts) == 3 else parts[3]
                if len(parts) == 4:
                    uuid.UUID(parts[2])
                parsed_start = datetime.fromisoformat(start_part)
                if parsed_start.tzinfo is None:
                    raise ValueError("selected_slot_id virtual start_utc must be timezone-aware")
            except Exception as exc:
                raise ValueError("selected_slot_id virtual format is invalid") from exc
            return normalized

        try:
            uuid.UUID(normalized)
        except Exception as exc:
            raise ValueError("selected_slot_id must be a UUID or virtual slot identifier") from exc
        return normalized

    @field_validator("timezone_source")
    @classmethod
    def validate_timezone_source(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip().lower()
        allowed = {"detected", "user_selected", "fallback_org"}
        if normalized not in allowed:
            raise ValueError(
                "timezone_source must be one of detected, user_selected, fallback_org"
            )
        return normalized

class ChatSessionCreate(BaseModel):
    org_id: UUID4

class ChatMessageResponse(BaseModel):
    session_id: UUID4
    action: Dict[str, Any]
