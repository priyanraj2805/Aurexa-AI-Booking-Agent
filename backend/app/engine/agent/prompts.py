"""Dynamic prompt builder for booking agent."""

import json
from datetime import datetime, timedelta
from typing import Dict, Any, List

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def build_system_prompt(
    widget_config: Dict[str, Any],
    field_definitions: List[Dict[str, Any]],
) -> str:
    """Build dynamic system prompt based on tenant config."""
    business_name = widget_config.get("business_name", "our business")
    persona = widget_config.get("persona", {})
    agent_name = persona.get("agent_name", "Booking Assistant")
    tone = persona.get("tone", "friendly and professional")

    tz_name = widget_config.get("timezone") or "UTC"
    now = None
    if ZoneInfo is not None:
        try:
            now = datetime.now(ZoneInfo(tz_name))
        except Exception:
            now = None
    if now is None:
        now = datetime.utcnow()
    today_iso = now.strftime("%Y-%m-%d")
    today_weekday = now.strftime("%A")
    tomorrow_iso = (now + timedelta(days=1)).strftime("%Y-%m-%d")

    # Build required fields list
    required = [f["name"] for f in field_definitions if f.get("required", True)]
    required_list = (
        ", ".join(required) if required else "name, email, phone, date, time"
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
            field_guidance.append(
                f"- {name}: A calendar date. ALWAYS output as YYYY-MM-DD. "
                f"Resolve relative dates (today, tomorrow, next monday, this friday) "
                f"using TODAY below."
            )
        else:
            field_guidance.append(f"- {name}: {ftype}")

    guidance_str = "\n".join(field_guidance) if field_guidance else ""

    return f"""You are {agent_name}, a {tone} booking assistant for {business_name}.

## DATE CONTEXT (timezone: {tz_name})
TODAY is {today_weekday}, {today_iso}. Tomorrow is {tomorrow_iso}.
When the user gives a relative date (e.g. "today", "tomorrow", "next monday",
"this friday", "in 3 days"), convert it to an absolute YYYY-MM-DD date using
TODAY and put it in extracted_fields.date. Never leave date empty if the user
named a day or relative date.

## YOUR TASK
Help users book appointments through natural conversation.
Extract information they provide. Answer their questions.

## REQUIRED FIELDS
{required_list}

## FIELD GUIDANCE (prevent mistakes)
{guidance_str}

## STRICT RULES
1. ONLY extract data explicitly stated in the user message
2. If user says "I don't want to give X", acknowledge it - DO NOT repeat the question
3. NEVER fabricate information not in the conversation
4. Extract ALL fields mentioned, not just the "next" one
5. "facebook", "instagram" etc go in NOTES, not email
6. If the user asks to CHANGE or CORRECT a field they already provided
   (e.g. "change email address", "my phone is actually..."), put the field
   name in "reopen_fields" so that field is re-asked. If they also give the
   new value in the same message, put it in "extracted_fields" instead
   (no need for reopen_fields in that case).

## OUTPUT FORMAT
Return JSON exactly:
{{
  "response_text": "your helpful reply",
  "extracted_fields": {{"field": "value"}},
  "reopen_fields": ["field_to_reask"],
  "user_intent": "proceed|resist|clarify|cancel|off_topic",
  "resistance_reason": "if resist: why",
  "confidence": 0.0-1.0
}}

## EXAMPLES
User: "I don't want to give my phone"
Output: {{"response_text": "I understand...", "extracted_fields": {{}}, "user_intent": "resist", "resistance_reason": "privacy_concern", "confidence": 0.85}}

User: "I'm John, email john@test.com"
Output: {{"response_text": "Thanks John!", "extracted_fields": {{"name": "John", "email": "john@test.com"}}, "user_intent": "proceed", "confidence": 0.95}}
"""


def build_context_block(
    collected_data: Dict[str, Any],
    missing_fields: List[str],
    status: str,
) -> str:
    """Build compact state context (~100 tokens)."""
    collected = json.dumps(collected_data, separators=(",", ":"))
    return f"""CURRENT STATE:
Collected: {collected}
Missing: {missing_fields}
Status: {status}"""
