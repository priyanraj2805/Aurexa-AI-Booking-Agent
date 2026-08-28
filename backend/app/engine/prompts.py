import json
from typing import Dict, Any, List, Optional
from app.schemas.agent import FieldDefinition


class PromptGenerator:
    """
    Constructs the exact string prompts to be sent to the LLM.
    We separate Intent Classification from Entity Extraction to allow
    for smaller, faster, cheaper models (like Llama 3 8B) to run in parallel.
    """

    @staticmethod
    def build_intent_prompt(user_message: str, current_state: Dict[str, Any]) -> str:
        """
        Builds the prompt that determines WHAT the user is trying to do.
        """
        valid_intents = [
            "START_BOOKING",
            "PROVIDE_DATA",
            "EDIT_DATA",
            "CANCEL_BOOKING",
            "ASK_QUESTION",
            "CONFIRM_BOOKING",
        ]

        prompt = f"""You are the Intent Classification subsystem for an AI Booking Assistant.
Your ONLY job is to analyze the user's message and determine their primary intent from a strict list.

Current conversational state (for context only):
{json.dumps(current_state, indent=2)}

User's Message: "{user_message}"

You MUST output EXACTLY ONE of the following precise strings, and absolutely nothing else:
{", ".join(valid_intents)}

Output:"""
        return prompt

    @staticmethod
    def _build_field_guidance(
        field_definitions: Optional[List[FieldDefinition]] = None,
        missing_fields: Optional[List[str]] = None,
        collected_data: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Build rich field extraction guidance from field definitions.
        Tells the LLM the name, type, label, and description of each field.
        """
        if not field_definitions:
            # Legacy fallback
            all_names = list((collected_data or {}).keys()) + (missing_fields or [])
            unique = list(dict.fromkeys(all_names)) or [
                "name",
                "email",
                "phone",
                "date",
                "time",
            ]
            return ", ".join(unique)

        lines = []
        for fd in sorted(field_definitions, key=lambda f: f.order):
            desc = f" - {fd.description}" if fd.description else ""
            opts = (
                f" (options: {', '.join(fd.options)})"
                if fd.type == "select" and fd.options
                else ""
            )
            req_str = "REQUIRED" if fd.required else "optional"
            lines.append(
                f'  - "{fd.name}" ({fd.type}, {req_str}): {fd.label}{desc}{opts}'
            )

        return "\n".join(lines)

    @staticmethod
    def build_unified_turn_prompt(
        user_message: str,
        missing_fields: List[str],
        collected_data: Dict[str, Any],
        current_intent: str,
        persona: Optional[Dict[str, Any]] = None,
        field_definitions: Optional[List[FieldDefinition]] = None,
        rag_context: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        UNIFIED PROMPT:
        Combines Intent Classification, Entity Extraction, and Conversational Response.
        This reduces round-trip latency by 50% on local models.

        persona: optional dict from OrgConfig.custom_prompts with keys:
            agent_name, greeting_message, tone, industry_context, custom_instructions
        field_definitions: optional list of FieldDefinition objects for dynamic extraction
        """
        fields_to_find = (
            ", ".join(missing_fields) if missing_fields else "None (Booking Complete)"
        )

        valid_intents = [
            "START_BOOKING",
            "PROVIDE_DATA",
            "EDIT_DATA",
            "CANCEL_BOOKING",
            "ASK_QUESTION",
            "CONFIRM_BOOKING",
        ]

        # Build persona block from OrgConfig.custom_prompts
        persona = persona or {}
        agent_name = persona.get("agent_name", "Booking Assistant")
        tone = persona.get("tone", "professional and friendly")
        industry_context = persona.get("industry_context", "")
        custom_instructions = persona.get("custom_instructions", "")

        persona_block = f"Your name is {agent_name}. Speak in a {tone} tone."
        if industry_context:
            persona_block += f"\nIndustry context: {industry_context}"
        if custom_instructions:
            persona_block += f"\nAdditional instructions: {custom_instructions}"

        # Strict business scope - derived dynamically from actual business data, no hardcoded service lists
        business_scope_block = ""
        scope_parts = []
        if industry_context and industry_context.strip():
            scope_parts.append(f"Industry: {industry_context.strip()}")
        # Use industry as the primary scope signal - no hardcoded service examples
        if scope_parts or (custom_instructions and custom_instructions.strip()):
            scope_text = "\n".join(f"- {p}" for p in scope_parts) if scope_parts else ""
            business_scope_block = (
                "### BUSINESS SCOPE (STRICT - DO NOT HALLUCINATE):\n"
                "You are a booking assistant for THIS BUSINESS ONLY. Your business's industry and services are described in the industry and company knowledge above.\n"
                "CRITICAL: When the user asks to book an appointment with a specific professional/service (e.g., 'book with engineer', 'beautician', 'doctor'), "
                "you must check if that professional/service is actually offered by THIS business as per your business scope. "
                "If the requested service/profession is NOT mentioned in and NOT reasonably related to your business's industry/services, it is OUT OF SCOPE.\n"
                + (scope_text + "\n" if scope_text else "")
                + "If the request is OUT OF SCOPE, you must NOT say 'Sure, I can book...' or 'Got it! To schedule...'. "
                "Instead, respond with a polite clarification like: 'We specialize in [your industry] bookings. For example, we can help with [your actual services]. "
                "The service you asked for is outside our scope. Would you like to book within our scope instead?' "
                "Then ask for the next missing field only if they confirm they want a valid service."
            )

        # Build field guidance - either rich definitions or legacy names
        field_guidance = PromptGenerator._build_field_guidance(
            field_definitions, missing_fields, collected_data
        )

        # Build extraction field names for JSON output hint
        if field_definitions:
            all_field_names = [fd.name for fd in field_definitions]
        else:
            all_names = list(collected_data.keys()) + missing_fields
            all_field_names = list(dict.fromkeys(all_names)) or [
                "name",
                "email",
                "phone",
                "date",
                "time",
            ]
        extraction_keys = ", ".join(all_field_names)

        # Use rich field block if definitions are available
        if field_definitions:
            field_block = f"""### FIELD DEFINITIONS (what to extract):
{field_guidance}"""
        else:
            field_block = f"Fields to extract: {extraction_keys}"

        # Build collected data summary for strong context anchoring
        collected_summary = (
            json.dumps(collected_data, indent=2) if collected_data else "(none yet)"
        )

        # Build the NEXT field to ask for - this is the #1 missing field
        next_field_label = ""
        if missing_fields and field_definitions:
            fd_map = {fd.name: fd for fd in field_definitions}
            first_missing = missing_fields[0]
            fd = fd_map.get(first_missing)
            if fd:
                next_field_label = fd.label
            else:
                next_field_label = first_missing.replace("_", " ").title()

        # Build RAG context block from retrieved+reranked chunks (vectorless).
        rag_block = ""
        if rag_context:
            from app.engine.knowledge_profile import format_source_context_line
            chunk_lines = []
            for i, ch in enumerate(rag_context, 1):
                content = (ch.get("content") or "").strip()
                source = ch.get("source_type") or "document"
                ctx_label = format_source_context_line(ch.get("metadata"))
                if ctx_label:
                    source += f" - {ctx_label}"
                if ch.get("source_url"):
                    source += f" ({ch['source_url']})"
                snippet = content[:800]
                chunk_lines.append(f"[{i}] (source: {source})\n{snippet}")
            rag_block = (
                "### COMPANY KNOWLEDGE CONTEXT (from uploaded docs / crawled website):\n"
                "Use this to answer questions about the business, services, pricing, "
                "hours, policies, and FAQs. If the answer isn't here, say so honestly.\n\n"
                + "\n\n".join(chunk_lines)
            )

        prompt = f"""You are the Unified Intelligence Core for a generalized AI Booking Assistant.
{persona_block}

{business_scope_block}

### SESSION CONTEXT (ALWAYS USE THIS - DO NOT HALLUCINATE):
- User's name (already collected): {collected_data.get("name", "(not provided yet)")}
- Currently Collected Data: {collected_summary}
- Missing Fields (in collection order): {fields_to_find}
- NEXT FIELD TO ASK FOR: {next_field_label}
- Last Known Intent: {current_intent}

        {field_block}

{rag_block}

### USER'S NEW MESSAGE:
"{user_message}"

### YOUR TASKS:
1. Determine the user's current INTENT from this list: {", ".join(valid_intents)}
2. EXTRACT ONLY THE NEXT MISSING FIELD from the message ({next_field_label}). 
   - ONLY extract a value if it is ACTUALLY PRESENT in the user's message.
   - DO NOT extract values for other missing fields - even if they are missing.
   - DO NOT extract values from previously collected data - those are already known.
   - DO NOT extract staff names (doctors) as the 'name' field.
   - If they provide new info for an existing field, extract it as an update.
   - For "select" type fields, the value MUST be one of the defined options.
   - If the user's message does NOT contain a value for the next missing field, leave extracted_entities EMPTY.
  3. GENERATE a polite, humble, and conversational RESPONSE to the user.
    - If the user asked a question or expressed concern (e.g. "why do I need this?", "what is this for?"), answer naturally and helpfully. Then gently guide them back to the booking. Do NOT force the next field if they're asking questions.
    - If the user just provided a value for a field, acknowledge it warmly, THEN ask for the next missing field: {next_field_label}
    - If the user is just chatting or greeting, respond naturally and guide them toward the booking.
    - CRITICAL: You MUST ask for EXACTLY ONE missing field at a time. Your response_message MUST ask for ONLY the next field: {next_field_label}. Do NOT mention any other missing fields. Do NOT list requirements. It is overwhelming.
    - If there are errors (e.g., invalid email), explain politely and ask they try again.
    - If ALL information is collected, tell them you're sending a verification code to their email.
    - You are a booking assistant for THIS BUSINESS ONLY as defined in BUSINESS SCOPE and COMPANY KNOWLEDGE CONTEXT above. You must NOT hallucinate services outside your scope. If user asks to book a professional/service NOT in your scope (e.g., engineer/beautician when you are Medical), do NOT say you can book it. Politely clarify your actual scope and ask if they want to proceed with a valid service within your scope.
    - If the user asks about the company, services, pricing, hours, or anything covered by your context - answer helpfully using provided knowledge. If the user asks something completely unrelated (general knowledge, trivia) - politely say that's outside your expertise and steer back to booking.
    - For schedule/business-hours answers, policy data is authoritative: do NOT hallucinate or invent open/closed hours.

### OUTPUT FORMAT:
You MUST return ONLY a valid JSON object with these keys:
- "intent": (string)
- "extracted_entities": (object - MUST contain ONLY the next field: {next_field_label})
- "response_message": (string - your polite reply asking ONLY for: {next_field_label})

Output ONLY valid JSON:"""
        return prompt
