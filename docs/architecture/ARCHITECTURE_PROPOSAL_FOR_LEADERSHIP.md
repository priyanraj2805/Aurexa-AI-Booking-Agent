# Architecture Proposal: Agent-Controlled Booking System

## 1) Executive Summary

This proposal replaces the current Python-heavy conversation state machine with an agent-controlled architecture.

- **Decision:** Use **Pydantic AI** as the agent framework.
- **Why:** It fits our existing FastAPI + Pydantic stack, supports typed tool-calling, and aligns with work already started in this repo.
- **Business outcome:** More natural conversations, better handling of user hesitation/questions, and lower long-term maintenance cost.

The implementation keeps all production-critical backend logic (DB, slot locking, OTP, email, calendar, audit logs) in Python tools, while moving conversation control to the LLM agent.

---

## 2) Why Change Is Needed

Today, conversation flow is controlled by a large Python state machine (`backend/app/engine/message_processor.py`) with many hardcoded overrides.

Main issues:
- LLM intent gets flattened/overridden, losing nuance (hesitation vs clarification vs off-topic).
- Keyword lists drive major actions, which is brittle for real user phrasing.
- Response templates frequently replace contextual LLM responses.
- A single large pipeline is hard to evolve safely.

Result: reliable but robotic behavior, lower conversational quality, and high maintenance burden.

---

## 3) Framework Decision

## Selected Framework: Pydantic AI

### Why Pydantic AI
- **Native type safety:** Fits our current Pydantic schemas and FastAPI codebase.
- **Tool-calling model:** Cleanly exposes backend operations as typed tools.
- **Lower implementation complexity:** Simpler than graph-first frameworks for this single-agent domain.
- **Existing direction in repo:** Agent-oriented artifacts already exist and can be reused conceptually.

### Why Not LangGraph (for v1)
- Strong for complex multi-agent workflows and advanced checkpoint orchestration.
- Adds extra graph/runtime complexity we do not need for the first version.

### Why Not Custom Direct API
- Highest control, but highest engineering and maintenance overhead.
- Rebuilds infrastructure already solved by mature frameworks.

---

## 4) Target System Architecture

```
User (Widget/WhatsApp)
        |
        v
Thin Orchestrator (FastAPI)
  - load session context
  - invoke booking agent
  - persist transcript/state
  - return ActionPayload
        |
        v
Booking Agent (Pydantic AI)
  - understands user intent
  - decides next conversational step
  - chooses widget directive
  - calls backend tools when needed
        |
        v
Tool Layer (Python, trusted operations)
  - validate_field
  - get_available_slots
  - lock_slot
  - create_booking
  - send_otp / verify_otp
  - cancel_booking
  - get_organization_info
        |
        v
Existing Services
  - PostgreSQL
  - Calendar integration
  - Email/OTP
  - Audit logging
```

### Responsibility Split
- **Agent (LLM):** conversation flow, intent understanding, response generation, directive selection.
- **Python tools:** deterministic validation, transactions, external side effects.
- **Orchestrator:** plumbing only (session I/O, observability, fallback routing).

---

## 5) Data Contracts (High-Level)

### Agent Output Contract
The agent returns a typed response that includes:
- `response_text`
- `extracted_fields`
- `user_intent` (rich intent)
- `widget_directive`
- `field_meta` (if needed)
- `tool_calls` / tool results
- `confidence`

### Frontend Contract
FastAPI still returns the same `ActionPayload` shape to the widget for compatibility.

---

## 6) Safety, Reliability, and Governance

Safety controls remain in Python:
- Field validation and sanitization.
- Slot locking and transaction safety.
- OTP verification gates before confirmation.
- Audit logging of critical booking actions.

Operational controls:
- Feature flags for gradual rollout.
- Circuit breaker/degraded mode fallback.
- Tool-level error contracts for predictable failure behavior.

---

## 7) Implementation Approach (Phased)

1. **Schema + interface alignment**
   - Finalize agent output schema and directive model.
2. **Tool layer implementation**
   - Wrap existing validated backend functions as typed tools.
3. **Agent orchestration integration**
   - Replace flow decisions in legacy processor with agent decisions.
4. **Progressive rollout**
   - Enable in stages via feature flags, compare behavior, then cut over.

Estimated build window: ~3-5 weeks including validation and rollout hardening.

---

## 8) What Leadership Is Being Asked to Approve

1. **Architecture direction:** Agent-controlled conversation + Python tool execution.
2. **Framework choice:** Pydantic AI for v1.
3. **Execution model:** Phased migration with feature flags and rollback safety.

---

## 9) Success Criteria

- Higher booking completion rate.
- Fewer user drop-offs due to rigid conversational flow.
- Reduced conversation-handling complexity in backend orchestration.
- No regression in booking integrity (double-booking, OTP, audit, calendar/email).

---

## 10) Scope Boundary (Important)

This proposal is architecture-only and approval-focused.
It intentionally avoids implementation-level task breakdown so leadership can review one clear decision document first.
