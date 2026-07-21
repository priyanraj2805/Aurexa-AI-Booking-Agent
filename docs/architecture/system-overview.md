# System Architecture Overview

> High-level design of the DotStark AI Booking Agent platform

This document provides a broad view of how the system components interact. For detailed component documentation, see [Backend Architecture](backend.md) and [Frontend Architecture](frontend.md).

---

## 1. High-Level Topology

The system consists of three main applications running independently:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           DEPLOYMENT VIEW                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐               │
│   │  Browser    │    │  Browser    │    │  Browser    │               │
│   │  (Customer) │    │  (Business) │    │  (Admin)   │               │
│   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘               │
│          │                   │                   │                      │
│          ▼                   ▼                   ▼                      │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐              │
│   │ Chat Widget │    │ Chat Widget │    │Admin Panel │              │
│   │  (Port 5174)│    │  (Embedded) │    │ (Port 3002)│              │
│   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘              │
│          │                   │                   │                      │
│          └───────────────────┼───────────────────┘                      │
│                              │                                          │
│                              ▼                                          │
│                    ┌─────────────────┐                                 │
│                    │  FastAPI Backend │                                 │
│                    │   (Port 8000)   │                                 │
│                    └────────┬────────┘                                 │
│                             │                                           │
│        ┌────────────────────┼────────────────────┐                   │
│        ▼                    ▼                    ▼                   │
│   ┌─────────┐        ┌─────────────┐      ┌──────────┐              │
│   │  Groq   │        │ PostgreSQL  │      │   SMTP   │              │
│   │   LLM   │        │  (State/DB) │      │  (Email) │              │
│   └─────────┘        └─────────────┘      └──────────┘              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### Component Ports

| Component | Port | Purpose |
|-----------|------|---------|
| Backend API | 8000 | Core business logic |
| Admin Panel | 3002 | Business dashboard |
| Chat Widget | 5174 | Dev/preview server |

---

## 2. Core Design Principle

### "Dumb LLM, Smart Python"

The platform uses a unique architectural pattern to minimize AI hallucinations while maintaining natural conversations:

```
┌─────────────────────────────────────────────────────────────────┐
│                    MESSAGE PROCESSING PIPELINE                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐ │
│  │  User    │───▶│   LLM    │───▶│ Validator │───▶│  State   │ │
│  │ Message  │    │ (Intent+ │    │ (Python)  │    │ Machine  │ │
│  └──────────┘    │ Entities)│    └──────────┘    └──────────┘ │
│                   └──────────┘          │            │           │
│                               ┌──────────┴──────────┐           │
│                               ▼                      ▼           │
│                        ┌──────────┐           ┌──────────┐      │
│                        │ Booking  │           │ Response │      │
│                        │ Engine   │           │ Builder  │      │
│                        └──────────┘           └──────────┘      │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**What the LLM does:**
- Classifies user intent (e.g., `START_BOOKING`, `PROVIDE_DATA`, `CONFIRM_BOOKING`)
- Extracts entities from natural language (e.g., "next Tuesday at 3pm" → `{date: "2026-03-24", time: "3:00 PM"}`)

**What Python does:**
- Validates extracted data (format, range, availability)
- Manages conversation state
- Executes bookings atomically
- Sends emails

---

## 3. Data Flow: Booking Request

### Step-by-Step Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     BOOKING REQUEST FLOW                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. CUSTOMER                                                            │
│     "I want to book an appointment for John, john@email.com,            │
│      +1-555-123-4567, March 17 at 10am"                                │
│     │                                                                    │
│     ▼                                                                    │
│  2. CHAT WIDGET (Web Component)                                         │
│     - Sends to backend via fetch()                                       │
│     - Header: X-Org-Api-Key: ds_xxx                                      │
│     │                                                                    │
│     ▼                                                                    │
│  3. BACKEND - process_message()                                         │
│     ┌─────────────────────────────────────────────────────────────┐     │
│     │ a) LLM Orchestrator                                         │     │
│     │    - Sends prompt to Groq API                              │     │
│     │    - Receives: {intent: "START_BOOKING", entities: {...}} │     │
│     │                                                             │     │
│     │ b) DataValidator                                           │     │
│     │    - Validates email format, phone, date                  │     │
│     │    - Returns errors if invalid                             │     │
│     │                                                             │     │
│     │ c) StateEvaluator                                          │     │
│     │    - Determines next required field                        │     │
│     │    - Returns widget directive (e.g., REQUIRE_TIME_SLOT)   │     │
│     └─────────────────────────────────────────────────────────────┘     │
│     │                                                                    │
│     ▼                                                                    │
│  4. DATABASE - chat_sessions                                             │
│     - Update collected_data (JSONB)                                     │
│     - Update missing_fields array                                       │
│     │                                                                    │
│     ▼                                                                    │
│  5. BACKEND - Response                                                   │
│     - Returns: {widget_directive, field_meta, message}                  │
│     │                                                                    │
│     ▼                                                                    │
│  6. CHAT WIDGET                                                          │
│     - Renders appropriate UI (text input, date picker, time slots)       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Multi-Tenancy Architecture

### Isolation Model

Each organization operates in complete isolation:

```
┌─────────────────────────────────────────────────────────────────┐
│                    MULTI-TENANT ISOLATION                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    DATABASE (PostgreSQL)                  │  │
│  │                                                            │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │  │
│  │  │  Org A     │  │  Org B     │  │  Org C     │     │  │
│  │  │  (ds_xxx)  │  │  (ds_yyy)  │  │  (ds_zzz)  │     │  │
│  │  ├─────────────┤  ├─────────────┤  ├─────────────┤     │  │
│  │  │ users      │  │ users      │  │ users      │     │  │
│  │  │ bookings   │  │ bookings   │  │ bookings   │     │  │
│  │  │ sessions   │  │ sessions   │  │ sessions   │     │  │
│  │  │ time_slots │  │ time_slots │  │ time_slots │     │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘     │  │
│  │                                                            │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Query Pattern:                                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ SELECT * FROM bookings WHERE org_id = :current_org_id   │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Authentication Methods

| User Type | Auth Method | Token Type |
|-----------|-------------|------------|
| Chat Widget | API Key | `X-Org-Api-Key` header |
| Admin Panel | JWT | Bearer token (HS256) |

---

## 5. Key Security Mechanisms

### 5.1 Rate Limiting

All critical endpoints protected with slowapi:

- `/chat/message/dispatch`: 30 requests/minute
- `/auth/otp/*`: 5 requests/minute

### 5.2 OTP Brute-Force Protection

```
Max 5 attempts per session
After 5 failures: session locked for 15 minutes
```

### 5.3 Concurrent Booking Prevention

```sql
-- Row-level locking
BEGIN;
SELECT * FROM time_slots 
WHERE id = :slot_id 
FOR UPDATE NOWAIT;  -- Fail immediately if locked

INSERT INTO bookings ...;
UPDATE time_slots SET is_booked = true WHERE id = :slot_id;
COMMIT;
```

---

## 6. Component Responsibilities

### Backend (FastAPI)

| Module | Responsibility |
|--------|----------------|
| `engine/message_processor.py` | Core conversation orchestration |
| `engine/orchestrator.py` | LLM interaction |
| `engine/validator.py` | Data validation |
| `engine/evaluator.py` | State machine |
| `crud/*` | Database operations |
| `api/*` | HTTP endpoints |

### Frontend - Admin Panel (Next.js)

| Page | Purpose |
|------|---------|
| `/dashboard` | Stats overview |
| `/dashboard/bookings` | View bookings |
| `/dashboard/conversations` | Chat history |
| `/dashboard/availability` | Time slot management |
| `/dashboard/integrations` | Widget embed code |

### Frontend - Chat Widget (Vanilla JS)

- Web Component (`<dotstark-booking-widget>`)
- Embeddable via single `<script>` tag
- Communicates with backend via REST API

---

## 7. External Integrations

### LLM Provider: Groq

- **Model:** `moonshotai/kimi-k2-instruct-0905`
- **Purpose:** Intent classification + entity extraction
- **Fallback:** Graceful degradation if API fails

### Email: SMTP (Gmail)

- **Uses:** App-specific passwords
- **Features:** OTP emails, booking confirmations

---

## 8. Related Documentation

| Document | What It Covers |
|----------|----------------|
| [Backend Architecture](backend.md) | Detailed backend components |
| [Frontend Architecture](frontend.md) | Admin panel & widget details |
| [Database Schema](database.md) | Tables, relationships, indexes |
| [LLM Engine](llm-engine.md) | AI integration deep dive |
| [API Overview](../api/overview.md) | Endpoint documentation |

---

*Level: Intermediate — You should be familiar with REST APIs and basic system design concepts.*
