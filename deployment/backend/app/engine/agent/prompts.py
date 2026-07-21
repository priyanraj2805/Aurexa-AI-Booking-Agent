"""Dynamic prompt builder for booking agent."""

import json
from typing import Dict, Any, List


def build_system_prompt(
    widget_config: Dict[str, Any],
    field_definitions: List[Dict[str, Any]],
) -> str:
    """Build dynamic system prompt based on tenant config."""
    business_name = widget_config.get("business_name", "our business")
    persona = widget_config.get("persona", {})
    agent_name = persona.get("agent_name", "Booking Assistant")
    tone = persona.get("tone", "friendly and professional")

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
            field_guidance.append(f"- {name}: A calendar date, not a time.")
        else:
            field_guidance.append(f"- {name}: {ftype}")

    guidance_str = "\n".join(field_guidance) if field_guidance else ""

    return f"""You are {agent_name}, a {tone} booking assistant for {business_name}.

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

## OUTPUT FORMAT
Return JSON exactly:
{{
  "response_text": "your helpful reply",
  "extracted_fields": {{"field": "value"}},
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
