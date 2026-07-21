# Target Architecture: Agent-Controlled Booking Flow

## Overview

This document outlines the target architecture where the **agent controls the conversation flow** and Python handles only **non-agentic backend operations** (database, calendar, email, etc.).

---

## Key Principle: Separation of Concerns

| Layer | Responsibility | Who Controls |
|-------|----------------|--------------|
| **Conversation Flow** | What to say, when to ask, how to handle resistance | **Agent (LLM)** |
| **Data Validation** | Format checking, business rules | Python |
| **Backend Operations** | DB writes, calendar sync, email sending | Python (via Tools) |
| **Widget Directives** | Which UI component to show | **Agent (LLM)** |

---

## Current vs Target Architecture

### Current: Python Controls Everything

```
User Message
    │
    ▼
┌───────────────────────────────────────┐
│        message_processor.py           │
│  (2600 lines of Python if/else)       │
├───────────────────────────────────────┤
│  1. Keyword detection                 │  ← Python decides
│  2. Session status guards             │  ← Python decides
│  3. Call agent (get text + entities)  │
│  4. Flatten intent                    │  ← Python decides
│  5. Override response with template   │  ← Python decides
│  6. StateEvaluator picks directive    │  ← Python decides
│  7. Execute booking/OTP/etc           │
└───────────────────────────────────────┘
    │
    ▼
ActionPayload(message, directive)
```

### Target: Agent Controls Conversation

```
User Message
    │
    ▼
┌───────────────────────────────────────┐
│           Booking Agent               │
│    (Pydantic AI with Tools)           │
├───────────────────────────────────────┤
│  • Full conversation history          │
│  • Organization context               │
│  • Persona/tone configuration         │
│  • Field definitions                  │
│  • Business hours awareness           │
├───────────────────────────────────────┤
│  Agent decides:                       │
│  • What to say (response_text)        │  ← Agent decides
│  • What intent user has               │  ← Agent decides
│  • What data was extracted            │  ← Agent decides
│  • What widget to show (directive)    │  ← Agent decides (NEW!)
│  • What tool to call (if any)         │  ← Agent decides
└───────────────────────────────────────┘
    │
    │  Tools for Backend Operations:
    │  ├─ validate_field(name, value)
    │  ├─ get_available_slots(date)
    │  ├─ lock_slot(slot_id)
    │  ├─ create_booking(data)
    │  ├─ send_otp(email)
    │  ├─ verify_otp(code)
    │  ├─ get_organization_info(topic)
    │  └─ cancel_booking(session_id)
    │
    ▼
AgentResponse(response_text, directive, tool_results)
    │
    ▼
┌───────────────────────────────────────┐
│      Thin Python Orchestrator         │
│    (100-200 lines, not 2600)          │
├───────────────────────────────────────┤
│  • Persist session state              │
│  • Persist transcript                 │
│  • Format final ActionPayload         │
│  • Handle circuit breaker fallback    │
└───────────────────────────────────────┘
    │
    ▼
ActionPayload(message, directive)
```

---

## Agent Response Schema (Revised)

The current schema explicitly says "NO next_action field":

```python
# Current (schemas.py)
class BookingResponse(BaseModel):
    """Critical: NO next_action field. Python decides actions."""
    response_text: str
    extracted_fields: ExtractedFields
    user_intent: UserIntent
    confidence: float
```

The revised schema gives the agent flow control:

```python
# Target (schemas.py)
class WidgetDirective(str, Enum):
    """What widget should the frontend show?"""
    TEXT_INPUT = "TEXT_INPUT"
    REQUIRE_NAME_INPUT = "REQUIRE_NAME_INPUT"
    REQUIRE_EMAIL_INPUT = "REQUIRE_EMAIL_INPUT"
    REQUIRE_PHONE_INPUT = "REQUIRE_PHONE_INPUT"
    REQUIRE_DATE_PICKER = "REQUIRE_DATE_PICKER"
    REQUIRE_TIME_SLOT_GRID = "REQUIRE_TIME_SLOT_GRID"
    REQUIRE_SELECT = "REQUIRE_SELECT"
    REQUIRE_TEXTAREA = "REQUIRE_TEXTAREA"
    REQUIRE_NUMBER_INPUT = "REQUIRE_NUMBER_INPUT"
    REQUIRE_OTP_MODAL = "REQUIRE_OTP_MODAL"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"
    BOOKING_CONFIRMED_CARD = "BOOKING_CONFIRMED_CARD"


class BookingResponse(BaseModel):
    """Agent-controlled response with flow decisions."""
    
    # What to say
    response_text: str = Field(description="Natural response to user")
    
    # What data was extracted
    extracted_fields: ExtractedFields = Field(default_factory=ExtractedFields)
    
    # User's intent (still useful for analytics/logging)
    user_intent: UserIntent
    
    # NEW: Agent controls the widget
    widget_directive: WidgetDirective = Field(
        description="What widget should appear next"
    )
    
    # NEW: Field metadata for the widget (if applicable)
    field_meta: Optional[Dict[str, Any]] = Field(
        None, description="Options, label, etc. for the next field"
    )
    
    # NEW: Tools the agent wants to invoke
    tool_calls: List[ToolCall] = Field(
        default_factory=list,
        description="Backend operations to perform"
    )
    
    # Confidence in extraction
    confidence: float = Field(ge=0, le=1)
    
    # Why user is resisting (if applicable)
    resistance_reason: Optional[str] = None
```

---

## Agent Tools

Instead of Python code directly calling CRUD functions, the agent invokes tools:

### Data Validation Tools

```python
@booking_agent.tool
async def validate_field(
    ctx: RunContext[SessionDeps],
    field_name: str,
    value: str
) -> Dict[str, Any]:
    """Validate a field value. Returns {valid: bool, normalized_value: str, error: str?}"""
    fd = ctx.deps.get_field_definition(field_name)
    valid, normalized, error = DataValidator.validate_single(
        field_name, value, fd, ctx.deps.timezone
    )
    return {"valid": valid, "normalized_value": normalized, "error": error}
```

### Slot Management Tools

```python
@booking_agent.tool
async def get_available_slots(
    ctx: RunContext[SessionDeps],
    date: str
) -> List[Dict[str, Any]]:
    """Get available time slots for a date."""
    slots = await compute_slot_inventory(ctx.deps.db, ctx.deps.org_id, date)
    return [{"time": s["time"], "available": s["available"]} for s in slots]

@booking_agent.tool
async def lock_slot(
    ctx: RunContext[SessionDeps],
    date: str,
    time: str
) -> Dict[str, Any]:
    """Lock a slot for booking. Returns {success: bool, slot_id: str?, error: str?}"""
    slot = await create_timeslot_lock(ctx.deps.db, ctx.deps.org_id, date, time)
    if slot:
        return {"success": True, "slot_id": str(slot.id)}
    return {"success": False, "error": "Slot not available"}
```

### Booking Tools

```python
@booking_agent.tool
async def create_booking(
    ctx: RunContext[SessionDeps],
    collected_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Create the final booking. Returns {success: bool, booking_id: str?, error: str?}"""
    try:
        booking = await attempt_booking(
            db=ctx.deps.db,
            session_id=ctx.deps.session_id,
            org_id=ctx.deps.org_id,
            **collected_data
        )
        return {"success": True, "booking_id": str(booking.id)}
    except Exception as e:
        return {"success": False, "error": str(e)}

@booking_agent.tool
async def cancel_booking(
    ctx: RunContext[SessionDeps],
    reason: Optional[str] = None
) -> Dict[str, Any]:
    """Cancel the current booking."""
    cancelled = await cancel_booking_by_session(ctx.deps.db, ctx.deps.session_id)
    return {"success": cancelled, "reason": reason}
```

### OTP Tools

```python
@booking_agent.tool
async def send_otp(
    ctx: RunContext[SessionDeps],
    email: str
) -> Dict[str, Any]:
    """Send OTP to email. Returns {success: bool, error: str?}"""
    try:
        code = await generate_otp(ctx.deps.db, ctx.deps.session_id)
        await send_otp_email(email, code, org_id=ctx.deps.org_id)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

@booking_agent.tool
async def verify_otp(
    ctx: RunContext[SessionDeps],
    code: str
) -> Dict[str, Any]:
    """Verify OTP code. Returns {valid: bool}"""
    is_valid = await crud_verify_otp(ctx.deps.db, ctx.deps.session_id, code)
    return {"valid": is_valid}
```

### Organization Context Tool

```python
@booking_agent.tool
async def get_organization_info(
    ctx: RunContext[SessionDeps],
    topic: str
) -> str:
    """Get information about the organization for a specific topic.
    
    Topics: services, hours, location, pricing, policies, about
    """
    org_context = ctx.deps.widget_config.get("organization_context", {})
    
    # The agent can use this to answer questions about the business
    if topic == "services":
        return org_context.get("services_description", "No service info available")
    elif topic == "hours":
        return org_context.get("business_hours", "No hours info available")
    # ... etc
    
    return "Information not available for this topic"
```

---

## System Prompt Structure

The agent needs a comprehensive system prompt that includes:

```python
def build_system_prompt(
    widget_config: Dict[str, Any],
    field_definitions: List[Dict[str, Any]],
    organization_context: Dict[str, Any],
) -> str:
    """Build the complete system prompt for the booking agent."""
    
    persona = widget_config.get("persona", {})
    agent_name = persona.get("agent_name", "Booking Assistant")
    tone = persona.get("tone", "professional and friendly")
    
    return f"""You are {agent_name}, a {tone} booking assistant.

## Your Capabilities
You can:
1. Collect booking information conversationally
2. Answer questions about the organization, services, and booking process
3. Validate data in real-time using the validate_field tool
4. Check slot availability using get_available_slots
5. Lock slots and create bookings
6. Handle OTP verification
7. Cancel or reschedule bookings

## Organization Context
{json.dumps(organization_context, indent=2)}

## Fields to Collect
{format_field_definitions(field_definitions)}

## Conversation Guidelines
- Be conversational, not robotic
- If user resists providing info, acknowledge their concern and explain why it's needed
- If user asks off-topic questions, answer if related to the organization, otherwise gently redirect
- If user provides multiple pieces of info at once, extract and validate all of them
- Use the appropriate widget_directive for the next piece of information needed

## Widget Directives
Choose the appropriate directive based on what you need next:
- TEXT_INPUT: General text (names, custom fields)
- REQUIRE_EMAIL_INPUT: Email address
- REQUIRE_PHONE_INPUT: Phone number
- REQUIRE_DATE_PICKER: Date selection
- REQUIRE_TIME_SLOT_GRID: Time slot selection (call get_available_slots first!)
- REQUIRE_SELECT: Multiple choice from options
- REQUEST_CONFIRMATION: Show booking summary for confirmation
- BOOKING_CONFIRMED_CARD: After successful booking

## Important Rules
1. ALWAYS validate data before considering it collected
2. NEVER invent information about the organization you don't have
3. If you need to call a tool, do so before responding
4. Your response_text should be natural and conversational
5. The widget_directive should match what you're asking for
"""
```

---

## Thin Orchestrator (Replaces message_processor.py)

```python
async def process_message(
    db: AsyncSession,
    org: Organization,
    org_config: OrgConfig,
    chat_session: ChatSessionState,
    message_text: str,
    background_tasks: Optional[BackgroundTasks] = None,
) -> ActionPayload:
    """
    Thin orchestrator that delegates to the agent.
    Only handles:
    - Building context for agent
    - Running agent
    - Persisting state
    - Circuit breaker fallback
    """
    
    # Build context
    field_definitions = parse_field_definitions(org_config.field_definitions)
    organization_context = build_organization_context(org, org_config)
    
    # Run agent
    try:
        agent_response = await run_booking_agent(
            user_message=message_text,
            session_id=chat_session.session_id,
            org_id=str(org.id),
            widget_config={
                "persona": org_config.custom_prompts,
                "organization_context": organization_context,
            },
            field_definitions=[fd.model_dump() for fd in field_definitions],
            collected_data=chat_session.collected_data,
            missing_fields=chat_session.missing_fields,
            db=db,
            timezone=str(org_config.timezone),
        )
    except CircuitBreakerOpen:
        # Degraded mode
        return await handle_degraded_mode(chat_session, message_text)
    
    # Update session state from agent response
    chat_session.collected_data.update(
        agent_response.extracted_fields.model_dump(exclude_none=True)
    )
    chat_session.current_intent = agent_response.user_intent.value
    
    # Recalculate missing fields
    chat_session.missing_fields = calculate_missing_fields(
        field_definitions, chat_session.collected_data
    )
    
    # Handle session status changes based on tool results
    for tool_result in agent_response.tool_results:
        if tool_result.name == "create_booking" and tool_result.result.get("success"):
            chat_session.session_status = "CONFIRMED"
        elif tool_result.name == "cancel_booking" and tool_result.result.get("success"):
            chat_session.session_status = "CANCELLED"
        elif tool_result.name == "verify_otp" and tool_result.result.get("valid"):
            chat_session.otp_verified = True
    
    # Persist state
    await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
    
    # Persist transcript
    await _save_transcript(
        db, chat_session.session_id, message_text,
        agent_response.response_text, agent_response.widget_directive.value
    )
    
    # Build ActionPayload
    return ActionPayload(
        message=agent_response.response_text,
        widget_directive=agent_response.widget_directive.value,
        field_meta=agent_response.field_meta,
    )
```

---

## What Gets Removed

| Current Code | Status | Reason |
|--------------|--------|--------|
| `CONFIRM_KEYWORDS` list | Remove | Agent understands context |
| `RESEND_KEYWORDS` list | Remove | Agent understands context |
| `CHANGE_DATE_KEYWORDS` list | Remove | Agent understands context |
| `CANCEL_KEYWORDS` list | Remove | Agent understands context |
| `_map_intent()` | Remove | Agent returns rich intent |
| `_build_context_aware_response()` | Remove | Agent generates responses |
| `_extract_entities_without_llm()` | Remove | Agent extracts reliably |
| `StateEvaluator.determine_next_action()` | Remove | Agent decides widgets |
| `_handle_post_booking()` (500 lines) | Simplify | Agent handles post-booking |
| Keyword detection blocks | Remove | Agent understands context |

---

## Migration Strategy

See `MIGRATION_PLAN.md` for the incremental migration approach.
