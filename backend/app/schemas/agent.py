from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional, Literal

# ══════════════════════════════════════════════════
#  FIELD DEFINITIONS — Rich field metadata schema
# ══════════════════════════════════════════════════

FIELD_TYPES = Literal[
    "text", "email", "phone", "date", "time", "select", "textarea", "number"
]
DATE_TIME_FIELD_ORDER = {"date": 0, "time": 1}


class FieldDefinition(BaseModel):
    """
    A single configurable booking field.
    Tenants define these to control what the agent collects,
    how it validates, and what UI the widget renders.
    """

    name: str = Field(
        ..., description="Unique key, used in collected_data (e.g. 'email', 'company')"
    )
    type: FIELD_TYPES = Field(
        default="text",
        description="Drives validation, LLM hints, and widget input type",
    )
    label: str = Field(
        ..., description="Human-readable display label (e.g. 'Email Address')"
    )
    required: bool = Field(
        default=True, description="Whether the agent must collect this before booking"
    )
    placeholder: str = Field(default="", description="Hint text shown in widget input")
    description: str = Field(
        default="", description="Help text for LLM prompt and widget tooltip"
    )
    icon: str = Field(default="📋", description="Emoji icon for display in summaries")
    options: List[str] = Field(
        default_factory=list, description="Choices for 'select' type fields"
    )
    order: int = Field(
        default=0, description="Collection/display priority (lower = first)"
    )

    @staticmethod
    def from_legacy_name(name: str) -> "FieldDefinition":
        """Convert a legacy flat field name string to a rich FieldDefinition."""
        presets = {
            "name": FieldDefinition(
                name="name",
                type="text",
                label="Full Name",
                placeholder="e.g. John Doe",
                icon="👤",
                order=0,
            ),
            "email": FieldDefinition(
                name="email",
                type="email",
                label="Email Address",
                placeholder="e.g. you@example.com",
                icon="📧",
                order=1,
            ),
            "phone": FieldDefinition(
                name="phone",
                type="phone",
                label="Phone Number",
                placeholder="e.g. +1 555-000-0000",
                icon="📞",
                order=2,
            ),
            "date": FieldDefinition(
                name="date",
                type="date",
                label="Preferred Date",
                placeholder="e.g. March 30, 2026",
                icon="📅",
                order=3,
            ),
            "time": FieldDefinition(
                name="time",
                type="time",
                label="Preferred Time",
                placeholder="e.g. 2:00 PM",
                icon="⏰",
                order=4,
            ),
        }
        if name in presets:
            return presets[name]
        return FieldDefinition(
            name=name, type="text", label=name.replace("_", " ").title(), order=99
        )


def parse_field_definitions(raw: Any) -> List[FieldDefinition]:
    """
    Parse OrgConfig.field_definitions from DB (JSONB).
    Handles: list of dicts (rich), list of strings (legacy), or None.
    """
    def _default_definitions() -> List[FieldDefinition]:
        return [
            FieldDefinition.from_legacy_name(n)
            for n in ["name", "email", "phone", "date", "time"]
        ]

    def _coerce_definition(item: Any) -> Optional[FieldDefinition]:
        if isinstance(item, FieldDefinition):
            return item
        if isinstance(item, str):
            name = item.strip()
            return FieldDefinition.from_legacy_name(name) if name else None
        if not isinstance(item, dict):
            return None

        name = str(item.get("name", "")).strip()
        if not name:
            return None

        normalized = dict(item)
        normalized["name"] = name
        normalized.setdefault("type", "text")
        normalized.setdefault("label", name.replace("_", " ").title())
        normalized.setdefault("required", True)
        normalized.setdefault("placeholder", "")
        normalized.setdefault("description", "")
        normalized.setdefault("icon", "📋")
        if not isinstance(normalized.get("options"), list):
            normalized["options"] = []
        if normalized.get("order") is None:
            normalized["order"] = 99

        try:
            return FieldDefinition(**normalized)
        except Exception:
            return FieldDefinition.from_legacy_name(name)

    if not raw:
        return _default_definitions()

    if isinstance(raw, list):
        if len(raw) == 0:
            return _default_definitions()

        definitions = []
        for item in raw:
            definition = _coerce_definition(item)
            if definition:
                definitions.append(definition)
        return definitions or _default_definitions()

    coerced_single = _coerce_definition(raw)
    if coerced_single:
        return [coerced_single]

    return _default_definitions()


def order_field_definitions(definitions: List[FieldDefinition]) -> List[FieldDefinition]:
    indexed = list(enumerate(definitions))
    return [
        fd
        for _, fd in sorted(
            indexed,
            key=lambda item: (
                1 if item[1].name in DATE_TIME_FIELD_ORDER else 0,
                DATE_TIME_FIELD_ORDER.get(item[1].name, item[1].order),
                item[1].order if item[1].name in DATE_TIME_FIELD_ORDER else 0,
                item[0],
            ),
        )
    ]


def get_required_field_names(definitions: List[FieldDefinition]) -> List[str]:
    """Extract ordered list of required field names from definitions."""
    required = order_field_definitions([fd for fd in definitions if fd.required])
    return [f.name for f in required]


# ══════════════════════════════════════════════════
#  SESSION & ACTION SCHEMAS
# ══════════════════════════════════════════════════


class ChatSessionState(BaseModel):
    """
    Represents the current conversational state and the entities
    extracted from the user thus far.
    """

    session_id: str
    org_id: str
    channel: str = "web"
    channel_user_id: Optional[str] = None
    collected_data: Dict[str, Any] = Field(default_factory=dict)
    missing_fields: List[str] = Field(default_factory=list)
    current_intent: Optional[str] = None
    session_status: str = "ACTIVE"
    otp_verified: bool = False
    pending_cancel: bool = False


class ActionPayload(BaseModel):
    """
    The standard DTO returned to any frontend (Web Widget, WhatsApp, etc)
    instructing it what to do next.
    """

    message: str
    widget_directive: Optional[str] = "NONE"
    options: Optional[List[str]] = None
    field_meta: Optional[Dict[str, Any]] = None  # Rich field metadata for the widget
    is_terminal: bool = False  # True if booking is confirmed or hard failed
