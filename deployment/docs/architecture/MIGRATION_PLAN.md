# Incremental Migration Plan: Python State Machine → Agentic Architecture

## Overview

This plan breaks the migration into **safe, incremental phases** that can be tested and deployed independently. Each phase is backwards-compatible until we flip the final switch.

---

## Phase Overview

| Phase | Name | Risk | Effort | Impact |
|-------|------|------|--------|--------|
| 0 | Preparation & Cleanup | Low | 1 day | Foundation |
| 1 | Schema Evolution | Low | 1 day | Enables agent control |
| 2 | Tool Implementation | Medium | 2-3 days | Backend operations |
| 3 | Prompt Engineering | Medium | 2-3 days | Agent decision quality |
| 4 | Response Selection | High | 2-3 days | Agent controls responses |
| 5 | Widget Directive Control | High | 2-3 days | Agent controls flow |
| 6 | Cleanup & Optimization | Low | 1-2 days | Remove legacy code |

**Total estimated time:** 10-15 days (not counting testing)

---

## Phase 0: Preparation & Cleanup

**Goal:** Clean up the codebase and establish baseline tests.

### Tasks

1. **Create baseline E2E tests**
   - Test happy path booking flow
   - Test cancellation flow
   - Test OTP flow
   - Test off-topic handling
   - Test user resistance handling

2. **Track the untracked prompts.py**
   ```bash
   git add backend/app/engine/agent/prompts.py
   git commit -m "Track agent prompts.py file"
   ```

3. **Clean up stale worktree**
   ```bash
   git worktree prune
   ```

4. **Add feature flag for new architecture**
   ```python
   # backend/app/core/config.py
   class Settings:
       # Existing
       AGENT_ENABLED: bool = True
       
       # New: gradual rollout
       AGENT_CONTROLS_RESPONSE: bool = False
       AGENT_CONTROLS_DIRECTIVE: bool = False
       AGENT_TOOLS_ENABLED: bool = False
   ```

### Deliverables
- [ ] E2E test suite with baseline coverage
- [ ] `prompts.py` tracked in git
- [ ] Feature flags in settings
- [ ] Clean git state

---

## Phase 1: Schema Evolution

**Goal:** Extend agent response schema to support flow control (without using it yet).

### Tasks

1. **Add WidgetDirective enum to schemas.py**
   ```python
   class WidgetDirective(str, Enum):
       TEXT_INPUT = "TEXT_INPUT"
       REQUIRE_NAME_INPUT = "REQUIRE_NAME_INPUT"
       REQUIRE_EMAIL_INPUT = "REQUIRE_EMAIL_INPUT"
       # ... etc
   ```

2. **Extend BookingResponse with optional new fields**
   ```python
   class BookingResponse(BaseModel):
       # Existing fields
       response_text: str
       extracted_fields: ExtractedFields
       user_intent: UserIntent
       confidence: float
       
       # NEW: Optional for backwards compatibility
       suggested_directive: Optional[WidgetDirective] = None
       suggested_field_meta: Optional[Dict[str, Any]] = None
   ```

3. **Update prompts.py to ask agent for suggested_directive**
   - Add section to system prompt explaining widget directives
   - Ask agent to suggest (but Python still decides)

4. **Add logging to compare agent suggestion vs Python decision**
   ```python
   logger.info(
       "Directive comparison",
       agent_suggested=agent_response.suggested_directive,
       python_decided=next_step["directive"],
       match=agent_response.suggested_directive == next_step["directive"]
   )
   ```

### Deliverables
- [ ] Extended schema (backwards compatible)
- [ ] Updated prompts
- [ ] Comparison logging
- [ ] Tests pass

---

## Phase 2: Tool Implementation

**Goal:** Implement agent tools for backend operations.

### Tasks

1. **Create tools.py with validation tool**
   ```python
   @booking_agent.tool
   async def validate_field(ctx, field_name: str, value: str) -> dict:
       # Wrap existing DataValidator
   ```

2. **Create slot management tools**
   ```python
   @booking_agent.tool
   async def get_available_slots(ctx, date: str) -> list:
       # Wrap existing compute_slot_inventory
   
   @booking_agent.tool
   async def lock_slot(ctx, date: str, time: str) -> dict:
       # Wrap existing create_timeslot_lock
   ```

3. **Create booking tools**
   ```python
   @booking_agent.tool
   async def create_booking(ctx, data: dict) -> dict:
       # Wrap existing attempt_booking
   
   @booking_agent.tool
   async def cancel_booking(ctx, reason: str = None) -> dict:
       # Wrap existing cancel_booking_by_session
   ```

4. **Create OTP tools**
   ```python
   @booking_agent.tool
   async def send_otp(ctx, email: str) -> dict:
       # Wrap existing OTP functions
   
   @booking_agent.tool
   async def verify_otp(ctx, code: str) -> dict:
       # Wrap existing verification
   ```

5. **Create organization info tool**
   ```python
   @booking_agent.tool
   async def get_organization_info(ctx, topic: str) -> str:
       # Return relevant org context
   ```

6. **Register tools with agent (behind feature flag)**
   ```python
   if settings.AGENT_TOOLS_ENABLED:
       agent = create_agent_with_tools()
   else:
       agent = create_agent_without_tools()
   ```

### Deliverables
- [ ] All tools implemented
- [ ] Tools registered behind flag
- [ ] Unit tests for each tool
- [ ] Integration tests

---

## Phase 3: Prompt Engineering

**Goal:** Optimize system prompt for agentic behavior.

### Tasks

1. **Restructure system prompt**
   - Add organization context section
   - Add field definitions section
   - Add conversation guidelines
   - Add widget directive decision guidelines
   - Add tool usage instructions

2. **Add few-shot examples to prompt**
   - Happy path example
   - User resistance example
   - Off-topic redirection example
   - Multi-field extraction example

3. **Test prompt with real conversations**
   - Collect sample conversations
   - Run through new prompt
   - Compare outputs

4. **Iterate on prompt based on results**

### Deliverables
- [ ] New system prompt in prompts.py
- [ ] Prompt test suite
- [ ] Documented prompt patterns

---

## Phase 4: Response Selection

**Goal:** Agent controls response text (biggest behavior change).

### Tasks

1. **Remove `_build_context_aware_response()` usage (behind flag)**
   ```python
   if settings.AGENT_CONTROLS_RESPONSE:
       msg = agent_response.response_text
   else:
       msg = _build_context_aware_response(...)
   ```

2. **Remove `force_llm_response` flag**
   - This was a band-aid; now default is agent response

3. **Update tests to expect agent-style responses**
   - More varied phrasing
   - Context-aware acknowledgments

4. **A/B test in production**
   - Roll out to small percentage
   - Monitor user satisfaction
   - Monitor completion rates

### Deliverables
- [ ] Feature flag implementation
- [ ] Updated tests
- [ ] A/B test infrastructure
- [ ] Rollout plan

---

## Phase 5: Widget Directive Control

**Goal:** Agent decides what widget to show next.

### Tasks

1. **Trust agent's suggested_directive (behind flag)**
   ```python
   if settings.AGENT_CONTROLS_DIRECTIVE:
       directive = agent_response.suggested_directive.value
   else:
       directive = StateEvaluator.determine_next_action(...)["directive"]
   ```

2. **Remove StateEvaluator usage**
   - But keep for fallback/validation

3. **Add guardrails for invalid directives**
   ```python
   # Agent suggested date picker but date already collected?
   if directive == "REQUIRE_DATE_PICKER" and "date" in collected_data:
       logger.warning("Agent suggested invalid directive, overriding")
       directive = agent_response.suggested_directive or "TEXT_INPUT"
   ```

4. **Update prompt to emphasize directive importance**

5. **Test extensively**
   - All widget types
   - Edge cases (no slots available, etc.)

### Deliverables
- [ ] Feature flag implementation
- [ ] Guardrails for invalid directives
- [ ] Comprehensive widget tests
- [ ] Rollout plan

---

## Phase 6: Cleanup & Optimization

**Goal:** Remove legacy code, optimize performance.

### Tasks

1. **Remove keyword detection lists**
   - `CONFIRM_KEYWORDS`
   - `RESEND_KEYWORDS`
   - `CHANGE_DATE_KEYWORDS`
   - `CANCEL_KEYWORDS`
   - etc.

2. **Remove `_map_intent()` function**

3. **Remove `_build_context_aware_response()` function**

4. **Remove `_extract_entities_without_llm()` function**

5. **Simplify `_handle_post_booking()` to use agent**

6. **Remove feature flags (make new behavior default)**

7. **Archive or delete StateEvaluator if no longer needed**

8. **Document new architecture**

### Deliverables
- [ ] Clean codebase
- [ ] Updated documentation
- [ ] Performance benchmarks
- [ ] Final test suite

---

## Risk Mitigation

### Rollback Strategy

Each phase has a clear rollback:
1. **Feature flags** — Disable the flag to revert
2. **Schema changes** — New fields are optional, can be ignored
3. **Tools** — Disable `AGENT_TOOLS_ENABLED`
4. **Response control** — Disable `AGENT_CONTROLS_RESPONSE`
5. **Directive control** — Disable `AGENT_CONTROLS_DIRECTIVE`

### Monitoring

Add metrics for:
- Agent response latency
- Directive match rate (agent vs Python)
- Booking completion rate
- User satisfaction (if trackable)
- Error rates per phase

### Testing Strategy

1. **Unit tests** — Each tool, each function
2. **Integration tests** — Full flow tests
3. **E2E tests** — Widget + backend
4. **Shadow mode** — Run both paths, compare results
5. **A/B testing** — Gradual rollout to real users

---

## Phase Dependencies

```
Phase 0 (Prep)
    │
    ▼
Phase 1 (Schema)
    │
    ├──────────────────┐
    ▼                  ▼
Phase 2 (Tools)    Phase 3 (Prompts)
    │                  │
    └────────┬─────────┘
             │
             ▼
      Phase 4 (Response)
             │
             ▼
      Phase 5 (Directive)
             │
             ▼
      Phase 6 (Cleanup)
```

---

## Success Criteria

| Metric | Current | Target |
|--------|---------|--------|
| message_processor.py lines | ~2600 | < 300 |
| Keyword detection blocks | 25+ | 0 |
| Agent decision points | 0 | 100% |
| Response template usage | ~80% | 0% |
| Booking completion rate | Baseline | ≥ Baseline |
| Error rate | Baseline | ≤ Baseline |

---

## Next Steps

1. **Review this plan with user**
2. **Decide on timeline and priorities**
3. **Start Phase 0 (Preparation)**
