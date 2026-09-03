from datetime import datetime, timezone
from typing import Dict, Any, List
from langchain_core.messages import SystemMessage
from app.schemas.agent import FieldDefinition, parse_field_definitions, order_field_definitions

def build_system_prompt(org_config: Dict[str, Any], user_timezone: str = "UTC") -> SystemMessage:
    """
    Build dynamic system prompt based on tenant config.
    """
    business_name = org_config.get("business_name", "our business")
    persona = org_config.get("persona", {})
    agent_name = persona.get("agent_name", "Booking Assistant")
    tone = persona.get("tone", "friendly and professional")
    custom_instructions = persona.get("custom_instructions", "")

    field_definitions = org_config.get("field_definitions") or []
    
    # Build required fields list
    required = [f["name"] for f in field_definitions if f.get("required", True)]
    required_list = (
        "- " + "\n- ".join(required) if required else "- name\n- email\n- phone\n- date\n- time"
    )

    # Build field guidance for anti-hallucination
    field_guidance = []
    for fd in field_definitions:
        name = fd.get("name", "")
        ftype = fd.get("type", "text")
        if ftype == "email":
            field_guidance.append(
                f"- {name}: Must contain @. 'facebook' is NOT an email."
            )
        elif ftype == "phone":
            field_guidance.append(f"- {name}: Must be 10+ digits.")
        elif ftype == "date":
            field_guidance.append(f"- {name}: A calendar date, not a time.")
        else:
            field_guidance.append(f"- {name}: {ftype}")

    guidance_str = "\n".join(field_guidance) if field_guidance else ""
    
    # Current Time Context
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    content = f"""You are {agent_name}, a {tone} booking assistant for {business_name}.

## ORG CONTEXT & STRICT RULES
{custom_instructions}

Current Server Time (UTC): {now_utc}
User's Timezone: {user_timezone}

## YOUR TASK
Help users book appointments through natural conversation.
You have access to tools to check availability, validate fields, and create bookings.
DO NOT hallucinate slot times. You MUST use the `check_available_slots` tool to see availability.
ASK FOR ONE FIELD AT A TIME in the order listed in FIELDS TO COLLECT. NEVER ask for multiple fields at once.

## FIELDS TO COLLECT (ONE-BY-ONE, in order)
{required_list}

## FIELD GUIDANCE (prevent mistakes)
{guidance_str}

## CONVERSATION RULE
CRITICAL: In your final text reply, ask for EXACTLY ONE missing field - the first missing field only. Example: if name is missing, say "Could you please share your full name?" - do NOT say "please provide name, email, phone, date and time".

## TOOL USAGE
- Use the tools provided to interact with the backend database.
- IMPORTANT: You must ONLY invoke tools using the native OpenAI JSON tool calling format.
- If you need to know what time it is for the user, convert the UTC time using the user's timezone.

## FINAL RESPONSE FORMAT
Whenever you want to send a final response back to the user (not a tool call), you MUST respond in plain text.
If your message requires a specific UI widget interaction from the user, append exactly one of these tags to the END of your message:
[WIDGET: DATE_PICKER] (when asking for a date)
[WIDGET: SLOT_PICKER] (when presenting slots or asking for a time)
[WIDGET: OTP_INPUT] (when asking for the OTP verification code)

Example text reply:
"Alright, what date are you looking to book your appointment? [WIDGET: DATE_PICKER]"
"""
    return SystemMessage(content=content)

class AgentContextBuilder:
    def __init__(self, db, org_config: Dict[str, Any]):
        self.db = db
        self.org_config = org_config

    async def get_field_definitions(self) -> List[Dict[str, Any]]:
        raw_definitions = self.org_config.get("field_definitions")
        if raw_definitions:
            definitions = order_field_definitions(parse_field_definitions(raw_definitions))
            return [definition.model_dump() for definition in definitions]

        required_fields = self.org_config.get("required_fields") or []
        if required_fields:
            definitions = order_field_definitions(
                [FieldDefinition.from_legacy_name(str(name)) for name in required_fields]
            )
            return [definition.model_dump() for definition in definitions]

        return []
