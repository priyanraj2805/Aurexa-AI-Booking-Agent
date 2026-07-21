# Backend Architecture

> Detailed breakdown of the FastAPI backend components

This document covers the backend application structure, key modules, and how they work together.

---

## 1. Project Structure

```
backend/
├── app/
│   ├── api/v1/
│   │   ├── endpoints/        # Route handlers
│   │   │   ├── auth.py      # Registration, login
│   │   │   ├── chat.py      # Message dispatch
│   │   │   ├── dashboard.py # Admin endpoints
│   │   │   ├── slots.py     # Availability
│   │   │   └── whatsapp.py # WhatsApp webhook
│   │   └── api.py           # Router assembly
│   │
│   ├── core/
│   │   ├── config.py        # Settings
│   │   ├── security.py       # JWT, password hashing
│   │   ├── limiter.py        # Rate limiting
│   │   ├── mail.py          # SMTP emails
│   │   └── whatsapp_client.py
│   │
│   ├── crud/
│   │   ├── chat.py          # Session operations
│   │   ├── booking.py       # Booking operations
│   │   └── user.py          # User operations
│   │
│   ├── engine/
│   │   ├── message_processor.py   # Main orchestration
│   │   ├── orchestrator.py         # LLM interaction
│   │   ├── validator.py            # Data validation
│   │   └── evaluator.py            # State machine
│   │
│   ├── models/              # SQLAlchemy models
│   │   ├── organization.py
│   │   ├── session.py
│   │   ├── booking.py
│   │   └── user.py
│   │
│   ├── schemas/             # Pydantic schemas
│   │   ├── chat.py
│   │   ├── booking.py
│   │   └── agent.py
│   │
│   └── db/
│       └── database.py      # Async connection
│
├── alembic/                 # Migrations
├── requirements.txt
└── run.py                  # Entry point
```

---

## 2. Core Request Flow

### Chat Message Pipeline

```
HTTP Request
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│  api/v1/endpoints/chat.py::dispatch_message()                   │
│                                                                 │
│  1. Validate API key (X-Org-Api-Key header)                    │
│  2. Get or create chat session                                  │
│  3. Call process_message() from engine                          │
└────────────────────────────┬────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  engine/message_processor.py::process_message()                 │
│                                                                 │
│  1. LLMOrchestrator.get_intent_and_entities()                  │
│     └── Calls Groq API, returns {intent, entities}              │
│                                                                 │
│  2. DataValidator.validate_entities()                          │
│     └── Validates format, range, business rules                 │
│                                                                 │
│  3. StateEvaluator.calculate_missing_fields()                  │
│     └── Determines what's still needed                          │
│                                                                 │
│  4. StateEvaluator.determine_next_action()                     │
│     └── Returns widget directive (TEXT_INPUT, DATE_PICKER,      │
│         TIME_SLOT_GRID, REQUEST_CONFIRMATION)                  │
│                                                                 │
│  5. Fetch time slots if needed (direct SQL query)              │
└────────────────────────────┬────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│  ActionPayload response                                         │
│  {message, widget_directive, field_meta, options}               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Key Modules

### 3.1 LLM Orchestrator (`engine/orchestrator.py`)

Handles all communication with the Groq API.

```python
class LLMOrchestrator:
    async def get_intent_and_entities(
        self,
        message: str,
        session_state: dict,
        field_definitions: list
    ) -> tuple[str, dict]:
        # 1. Build prompt with field definitions
        # 2. Send to Groq API
        # 3. Parse response into intent + entities
```

**Prompt Structure:**
```
System: You are a booking assistant. Extract intent and entities.
User: "I want to book for john@email.com tomorrow"

Output: {
    "intent": "START_BOOKING",
    "entities": {"email": "john@email.com", "date": "2026-03-18"}
}
```

### 3.2 Data Validator (`engine/validator.py`)

Validates extracted entities using regex and business rules.

```python
class DataValidator:
    @staticmethod
    def validate_entities(
        entities: dict,
        field_definitions: list,
        timezone: str = "UTC"
    ) -> tuple[dict, dict]:
        # Returns: (valid_data, errors)
```

**Validation Rules:**
| Field | Rule |
|-------|------|
| email | RFC 5322 regex |
| phone | E.164 format |
| date | Must be future, within max_booking_days |
| time | Must match available slots |

### 3.3 State Evaluator (`engine/evaluator.py`)

Determines conversation flow based on collected data.

```python
class StateEvaluator:
    TYPE_DIRECTIVE_MAP = {
        "text": "TEXT_INPUT",
        "email": "REQUIRE_EMAIL_INPUT",
        "phone": "REQUIRE_PHONE_INPUT",
        "date": "REQUIRE_DATE_PICKER",
        "time": "REQUIRE_TIME_SLOT_GRID",
        "select": "REQUIRE_SELECT",
    }
```

### 3.4 Message Processor (`engine/message_processor.py`)

The main orchestration module that ties everything together.

```python
async def process_message(
    db: AsyncSession,
    org: Organization,
    chat_session: ChatSessionState,
    message: str,
    intent: str | None,
    require_otp: bool,
) -> ActionPayload:
    # Full pipeline: LLM → Validate → State → Response
```

---

## 4. API Endpoints

### Authentication

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/auth/register` | None | Create account + org |
| POST | `/api/v1/auth/login` | None | Get JWT token |
| POST | `/api/v1/auth/security/otp/generate` | None | Send OTP |
| POST | `/api/v1/auth/security/otp/verify` | None | Verify OTP |

### Chat

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/chat/message/dispatch` | API Key | Process message |

### Dashboard

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/dashboard/stats` | JWT | Get stats |
| GET | `/api/v1/dashboard/recent-bookings` | JWT | List bookings |
| GET | `/api/v1/dashboard/conversations` | JWT | List sessions |
| GET/PUT | `/api/v1/dashboard/agent-config` | JWT | Agent settings |

### Availability

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/api/v1/dashboard/availability` | JWT | Get slots |
| POST | `/api/v1/dashboard/availability` | JWT | Create slot |
| DELETE | `/api/v1/dashboard/availability/{id}` | JWT | Delete slot |

---

## 5. Database Operations

### Session Management (`crud/chat.py`)

```python
async def get_or_create_session(db, org_id, user_id) -> ChatSession:
    # Creates new session if none exists
    # Updates session state in JSONB column
```

### Booking (`crud/booking.py`)

```python
async def attempt_booking(db, session, slot_id, customer_data):
    # 1. SELECT FOR UPDATE on time_slot (atomic lock)
    # 2. Verify slot still available
    # 3. INSERT booking
    # 4. UPDATE slot to booked
    # 5. COMMIT transaction
```

---

## 6. Middleware & Security

### Rate Limiting

Configured in `core/limiter.py` using slowapi:

```python
@router.post("/chat/message/dispatch")
@limiter.limit("30/minute")
async def dispatch_message(request: Request, ...):
    ...
```

### JWT Authentication

```python
# Token payload
{
    "sub": user_id,
    "org_id": org_id,
    "exp": expiration_timestamp
}
```

### API Key Validation

```python
# Widget requests use header
X-Org-Api-Key: ds_vZZajXyBCoXNbzl1op0pouNW5GiWx3cA
```

---

## 7. Error Handling

### Error Response Format

```json
{
    "detail": "Error message here"
}
```

### HTTP Status Codes

| Code | Usage |
|------|-------|
| 200 | Success |
| 201 | Created |
| 400 | Validation error |
| 401 | Unauthorized |
| 403 | Invalid API key |
| 404 | Not found |
| 429 | Rate limited |
| 500 | Internal error |

---

## 8. Running the Backend

```bash
# Development
cd backend
python run.py

# Or with uvicorn
uvicorn app.main:app --reload --port 8000

# Production
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## 9. Related Documents

| Document | What It Covers |
|----------|----------------|
| [System Overview](system-overview.md) | High-level architecture |
| [Frontend Architecture](frontend.md) | Admin panel & widget |
| [Database Schema](database.md) | Tables & relationships |
| [LLM Engine](llm-engine.md) | AI integration details |
| [API Reference](../api/overview.md) | Endpoint details |

---

*Level: Intermediate to Advanced — You should be familiar with FastAPI, async Python, and SQL.*
