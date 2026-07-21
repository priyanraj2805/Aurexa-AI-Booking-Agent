# Architecture Audit: Python Override Points

## Executive Summary

The current booking agent architecture is a **"pseudo-agentic" system** — it has an LLM agent component (`backend/app/engine/agent/`) but Python code in `message_processor.py` makes all the actual decisions. The LLM's role is reduced to:

1. Text generation (response phrasing)
2. Entity extraction (pulling fields from user messages)
3. Intent classification (that gets immediately flattened/overridden)

**The LLM never controls the conversation flow.** Every decision about what happens next is made by Python if/else logic.

---

## Architecture Diagram: Current State

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          message_processor.py                           │
│                         (~2600 lines of Python)                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  User Message → [20+ Python keyword checks] → Decision Tree             │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                    run_booking_agent()                          │   │
│  │  - Gets response_text, extracted_fields, user_intent            │   │
│  │  - Returns BookingResponse                                       │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│       │                                                                 │
│       ▼                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              PYTHON OVERRIDES EVERYTHING                         │   │
│  │                                                                   │   │
│  │  1. _map_intent() flattens rich intents to legacy strings        │   │
│  │  2. Keyword detection overrides intent (CONFIRM_KEYWORDS, etc)   │   │
│  │  3. Response selection ignores agent response in many cases      │   │
│  │  4. _build_context_aware_response() generates template msgs      │   │
│  │  5. StateEvaluator.determine_next_action() decides widget type   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│       │                                                                 │
│       ▼                                                                 │
│  ActionPayload(message, widget_directive, field_meta)                   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Override Points

### 1. Intent Flattening (`_map_intent`, line 597-606)

The agent returns a rich `UserIntent` enum:
- `PROCEED` — User is cooperating
- `RESIST` — User refuses to provide info
- `CLARIFY` — User asks a question
- `CANCEL` — User wants to stop
- `OFF_TOPIC` — Unrelated discussion

**Python immediately flattens this:**
```python
def _map_intent(agent_intent: UserIntent) -> str:
    mapping = {
        UserIntent.PROCEED: "PROVIDE_DATA",
        UserIntent.RESIST: "ASK_QUESTION",    # ← Loses meaning!
        UserIntent.CLARIFY: "ASK_QUESTION",   # ← Same as RESIST!
        UserIntent.CANCEL: "CANCEL_BOOKING",
        UserIntent.OFF_TOPIC: "ASK_QUESTION", # ← Same as RESIST!
    }
    return mapping.get(agent_intent, "PROVIDE_DATA")
```

**Problem:** Three different intents (`RESIST`, `CLARIFY`, `OFF_TOPIC`) all become `"ASK_QUESTION"`. The nuance the agent detected is thrown away.

---

### 2. Keyword-Based Intent Override (lines 144-181, 757-773)

Python has hardcoded keyword lists that override whatever intent the agent detected:

```python
CONFIRM_KEYWORDS = [
    "yes", "confirm", "go ahead", "book it", "proceed", "complete",
    "do it", "make it", "sure", "finalize", ...  # 25+ keywords
]

RESEND_KEYWORDS = ["resend", "re-send", "send again", "new code", ...]

CHANGE_DATE_KEYWORDS = [
    "change the date", "change date", "different date", ...
]
```

**Override logic (line 759):**
```python
if not chat_session.missing_fields and any(
    kw in user_msg_lower for kw in CONFIRM_KEYWORDS
):
    intent = "CONFIRM_BOOKING"  # Ignores what agent said
```

**Problem:** The agent might understand "sure" in context ("Sure, why do you need my phone?") but Python blindly interprets it as confirmation.

---

### 3. Response Selection Override (`_build_context_aware_response`, lines 465-576)

Even when the agent generates a thoughtful, contextual response, Python often replaces it with a template:

```python
def _build_context_aware_response(...):
    """
    Generate a deterministic, context-aware response that stays in sync
    with the widget directive. Overrides the LLM response when:
    - The LLM asks for a field that isn't the next missing one
    - The LLM doesn't acknowledge newly provided data
    - The LLM response contradicts the directive
    """
    
    if directive == "REQUIRE_TIME_SLOT_GRID":
        # Template response, ignores agent's response_text
        return f"Thank you, {user_name}. Please choose your preferred time slot..."
    
    if directive == "REQUIRE_DATE_PICKER":
        return "Please choose your preferred date for the appointment."
    
    if directive == "REQUIRE_EMAIL_INPUT":
        return "Please share your email address so I can send you a verification code."
    
    # ... more templates ...
```

**When this gets used (line 1016-1029):**
```python
if valid_data and not force_llm_response:
    msg = _build_context_aware_response(...)  # Template wins
else:
    msg = llm_generated_msg  # Only if no valid data extracted
```

**Problem:** The agent's personality, tone, and contextual nuance are thrown away for generic templates.

---

### 4. Forced LLM Response Flag (lines 698-710)

There's a `force_llm_response` flag that's supposed to let the agent's response through, but it's only set in very narrow cases:

```python
if decision.action == "ADDRESS_CONCERN":
    force_llm_response = True  # User resisting

if agent_response.user_intent == UserIntent.OFF_TOPIC:
    force_llm_response = True  # Off-topic
```

**Problem:** This is a band-aid. The default behavior is still "Python overrides agent."

---

### 5. StateEvaluator Controls Flow (line 933)

```python
next_step = StateEvaluator.determine_next_action(
    chat_session.missing_fields, 
    chat_session.current_intent, 
    field_definitions
)
```

The `StateEvaluator` decides:
- What widget to show (`REQUIRE_EMAIL_INPUT`, `REQUIRE_DATE_PICKER`, etc.)
- What field to collect next
- When to show confirmation

**The agent has no say in this.** It can't decide "the user seems confused, let me clarify" or "maybe I should ask about their preferences first."

---

### 6. DataValidator Filters Agent Extraction (lines 881-902)

The legacy path filters what the agent extracted:

```python
# Filter out LLM hallucinations: only keep entities for the primary
# missing field (or fields the user could reasonably have answered).
primary_missing = chat_session.missing_fields[0] if chat_session.missing_fields else None
if raw_entities and primary_missing:
    filtered_entities = {}
    for field, value in raw_entities.items():
        if field == primary_missing:
            filtered_entities[field] = value
        elif field in chat_session.collected_data:
            filtered_entities[field] = value
        elif field in {"name", "email", "phone"}:
            filtered_entities[field] = value
    raw_entities = filtered_entities
```

**Problem:** If the user says "I'm John, my email is john@example.com, and I'd like to book for Tuesday" — the agent might extract all three, but Python throws away some because they weren't the "primary missing field."

---

### 7. Fallback Extraction Without LLM (lines 309-402)

When the LLM "fails," Python tries to extract entities itself using regex:

```python
def _extract_entities_without_llm(
    message_text: str,
    missing_fields: list[str],
    field_definitions: list[FieldDefinition],
    ...
) -> Dict[str, Any]:
    # ... 100 lines of regex parsing ...
```

**Problem:** This is solving the wrong problem. Instead of fixing why the LLM isn't being used properly, there's a parallel Python extraction system.

---

### 8. Post-Booking Python State Machine (lines 1222-1679)

After booking is confirmed/cancelled/expired, there's another massive Python state machine:

```python
CANCEL_KEYWORDS = ["cancel my booking", "cancel it", "i want to cancel", ...]
CANCEL_SOFT_KEYWORDS = ["cancel", "don't want", "changed my mind", ...]
RESCHEDULE_KEYWORDS = ["reschedule", "change date", "change time", ...]
REBOOK_KEYWORDS = ["book", "new booking", "schedule", ...]
```

This includes a secondary LLM call (`_llm_post_booking_self_check`) that's only used as a "secondary signal" — the keyword matching still has priority.

---

### 9. OTP Verification Bypass (lines 819-879)

OTP verification is handled entirely in Python with regex:

```python
otp_pattern = re.compile(r"\b(\d{6})\b")
match = otp_pattern.search(message_text)
if match:
    otp_code = match.group(1)
    is_valid = await crud_verify_otp(db, chat_session.session_id, otp_code)
```

The agent isn't involved in OTP flows at all.

---

### 10. Session Status Guards (lines 636-651)

Hard Python guards based on session status:

```python
if chat_session.session_status == "CONFIRMING":
    msg = "Your booking is being processed right now. Please wait a moment..."
    return ActionPayload(...)

if chat_session.session_status in ("CONFIRMED", "CANCELLED", "EXPIRED"):
    return await _handle_post_booking(...)
```

The agent never sees these messages — Python intercepts them before the agent runs.

---

## What the Agent COULD Do (But Doesn't)

The agent infrastructure exists but is severely underutilized:

| Capability | Current State | Ideal State |
|------------|---------------|-------------|
| Conversation memory | Exists in `memory.py` | Should drive context-aware responses |
| Intent classification | Works, then gets flattened | Should control flow decisions |
| Confidence scoring | Returned but mostly ignored | Should trigger clarification |
| Resistance detection | Detected, partially used | Should adapt questioning strategy |
| Tools | Defined but not invoked | Should perform DB/calendar ops |
| Organization context | Passed but not utilized | Should answer tenant questions |
| Persona customization | Passed but templates override | Should shape response style |

---

## Schema Design Problem

From `schemas.py` (line 49-54):

```python
class BookingResponse(BaseModel):
    """Structured response from LLM.

    Critical: NO next_action field. Python decides actions based on
    state + intent, not LLM.
    """
```

**This is the design decision that prevents agentic behavior.** The schema explicitly says "Python decides actions, not LLM."

---

## Summary of Override Categories

| Category | Count | Severity |
|----------|-------|----------|
| Intent flattening | 1 | Critical |
| Keyword-based overrides | 5+ | Critical |
| Response template replacement | 10+ directives | High |
| Entity filtering | 1 | Medium |
| Flow control (StateEvaluator) | 1 | Critical |
| Post-booking state machine | 1 | High |
| Session status guards | 3 | Medium |

**Total Python Decision Points:** ~25+

**LLM Decision Points:** 0 (text generation only)

---

## Next Steps

1. **Research** — Look at how LangGraph, Pydantic AI patterns, and similar frameworks handle agentic flow control
2. **Design** — Create a target architecture where the agent controls conversation flow
3. **Plan** — Develop an incremental migration path (we can't rewrite everything at once)
4. **Implement** — Start with the highest-impact changes (intent handling, response selection)

---

## Files to Modify

| File | Purpose | Changes Needed |
|------|---------|----------------|
| `message_processor.py` | Main state machine | Needs major refactoring |
| `engine/agent/schemas.py` | Agent response format | Add flow control fields |
| `engine/agent/core.py` | Agent runner | Add tool execution |
| `engine/agent/tools.py` | Agent tools | Implement DB/calendar tools |
| `engine/evaluator.py` | State evaluation | May be removable if agent controls flow |
| `engine/orchestrator.py` | Legacy LLM calls | Can be deprecated |
