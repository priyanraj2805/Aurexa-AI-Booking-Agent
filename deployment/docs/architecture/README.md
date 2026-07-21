# Architecture Refactoring Summary

## TL;DR

Your booking agent is a **"sudo AI agent"** — it has an LLM but Python makes all the decisions. The fix is to invert this: let the agent control conversation flow and use Python only for backend operations (DB, email, calendar).

---

## The Problem (Visual)

```
CURRENT: Python Controls Everything
==========================================
User: "Hi, I'm John"
         │
         ▼
    ┌─────────────────────────────────┐
    │  message_processor.py (2600 LOC)│
    │  ─────────────────────────────  │
    │  if "yes" in msg: CONFIRM       │  ← Python decides
    │  if "cancel" in msg: CANCEL     │  ← Python decides
    │  if "resend" in msg: OTP        │  ← Python decides
    │  ...25 more keyword checks...   │  ← Python decides
    │                                 │
    │  agent = run_booking_agent()    │
    │  // Gets: intent, entities, text│
    │                                 │
    │  intent = _map_intent(intent)   │  ← Python overrides
    │  msg = build_template_response()│  ← Python overrides
    │  directive = StateEvaluator()   │  ← Python decides
    └─────────────────────────────────┘
         │
         ▼
    "Thank you, John. Please share your email."
    (Same template every time, regardless of context)


TARGET: Agent Controls Conversation
==========================================
User: "Hi, I'm John"
         │
         ▼
    ┌─────────────────────────────────┐
    │  Booking Agent (Pydantic AI)    │
    │  ─────────────────────────────  │
    │  • Full conversation history    │
    │  • Organization context         │
    │  • Persona/tone settings        │
    │  • Tool access (DB, calendar)   │
    │                                 │
    │  → Validates name via tool      │
    │  → Decides next question        │
    │  → Generates contextual response│
    │  → Picks widget directive       │
    └─────────────────────────────────┘
         │
         ▼
    "Hi John! Great to have you here. 
     To get your appointment booked, 
     could you share your email? I'll 
     send a quick verification code."
    (Contextual, personalized)
```

---

## Documents Created

| Document | Purpose |
|----------|---------|
| [`docs/architecture/ARCHITECTURE_AUDIT.md`](./ARCHITECTURE_AUDIT.md) | Complete list of 25+ Python override points |
| [`docs/architecture/TARGET_ARCHITECTURE.md`](./TARGET_ARCHITECTURE.md) | Target design with agent tools and thin orchestrator |
| [`docs/architecture/MIGRATION_PLAN.md`](./MIGRATION_PLAN.md) | 6-phase incremental migration (10-15 days) |

---

## Key Findings

### 1. Intent Flattening
The agent returns rich intents (`RESIST`, `CLARIFY`, `OFF_TOPIC`) but Python immediately flattens them:
```python
UserIntent.RESIST → "ASK_QUESTION"
UserIntent.CLARIFY → "ASK_QUESTION"  # Same!
UserIntent.OFF_TOPIC → "ASK_QUESTION"  # Same!
```
The nuance is thrown away.

### 2. Keyword Override
25+ keyword lists override agent decisions:
```python
CONFIRM_KEYWORDS = ["yes", "sure", "ok", ...]  # 25 keywords
CANCEL_KEYWORDS = ["cancel", "stop", ...]
```
"Sure, why do you need my phone?" → Python interprets as confirmation.

### 3. Template Responses
Even when the agent generates contextual responses, Python replaces them:
```python
if valid_data and not force_llm_response:
    msg = _build_context_aware_response()  # Template wins
```

### 4. Schema Design
The schema explicitly prevents agent control:
```python
class BookingResponse:
    """Critical: NO next_action field. Python decides actions."""
```

---

## Migration Approach

**6 Phases, Feature-Flagged**

1. **Phase 0: Preparation** — Tests, clean git, add feature flags
2. **Phase 1: Schema Evolution** — Add optional flow control fields
3. **Phase 2: Tools** — Implement agent tools for DB/calendar
4. **Phase 3: Prompts** — Engineer prompts for agentic behavior
5. **Phase 4: Response Control** — Agent generates responses
6. **Phase 5: Directive Control** — Agent decides widgets
7. **Phase 6: Cleanup** — Remove 2000+ lines of legacy code

Each phase has a **rollback** via feature flag.

---

## What Changes

| Before | After |
|--------|-------|
| 2600 LOC state machine | ~300 LOC thin orchestrator |
| 25+ keyword lists | 0 keyword lists |
| Python decides everything | Agent decides conversation |
| Template responses | Contextual, personalized |
| Intent flattening | Rich intent handling |
| Unused org context | Context-aware answers |
| Unused persona settings | Personality in responses |

---

## What Stays the Same

- Database models
- API endpoints
- Widget/frontend
- Email templates
- Calendar integrations
- Existing business logic (wrapped in tools)

---

## Next Steps

1. **Review these documents** — Do they make sense?
2. **Decide on priorities** — Which phase to start with?
3. **Set timeline** — 10-15 days estimated
4. **Start Phase 0** — Baseline tests and cleanup

---

## Questions for You

Before we start:

1. **Timeline** — Do you want to tackle this now or after other features?
2. **Rollout strategy** — A/B test or big bang switch?
3. **Off-topic handling** — Should we abandon the off-topic/resist branch work? (It was treating symptoms, not the root cause)
4. **Testing** — Do you have existing E2E tests we should baseline against?
