# API Overview

> Complete guide to the DotStark Booking API

This document covers API authentication, request/response patterns, and core endpoints.

---

## 1. Base URL

```
Production: https://api.yourdomain.com
Development: http://localhost:8000
```

All API endpoints are prefixed with `/api/v1`

---

## 2. Authentication

The API supports two authentication methods:

### 2.1 API Key (for Chat Widget)

Used by the embedded chat widget to identify the organization.

```
Header: X-Org-Api-Key: ds_vZZajXyBCoXNbzl1op0pouNW5GiWx3cA
```

**Key Format:** `ds_` prefix + 28 random characters

### 2.2 JWT Bearer Token (for Admin Panel)

Used by the admin dashboard for authenticated users.

```
Header: Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Token Details:**
- Algorithm: HS256
- Expiry: 7 days
- Payload:
```json
{
  "sub": "user-uuid",
  "org_id": "org-uuid",
  "exp": 1700000000
}
```

---

## 3. Request Format

### Headers

```http
Content-Type: application/json
Accept: application/json

# For widget requests:
X-Org-Api-Key: ds_xxx

# For authenticated requests:
Authorization: Bearer <jwt_token>
```

### Request Body

All POST/PUT requests use JSON:

```json
{
  "field1": "value1",
  "field2": "value2"
}
```

---

## 4. Response Format

### Success Response

```json
{
  "session_id": "uuid-string",
  "action": {
    "message": "Bot response message",
    "widget_directive": "TEXT_INPUT",
    "field_meta": {
      "name": "field_name",
      "type": "text",
      "label": "Field Label",
      "placeholder": "Placeholder text"
    },
    "options": null,
    "is_terminal": false
  }
}
```

### Error Response

```json
{
  "detail": "Error message describing what went wrong"
}
```

---

## 5. Core Endpoints

### 5.1 Chat Message Dispatch

**Process a user message and get AI response.**

```http
POST /api/v1/chat/message/dispatch
```

| Auth | Rate Limit |
|------|-------------|
| API Key | 30/min |

**Request:**

```json
{
  "session_id": "uuid-string-or-null",
  "message": "I want to book an appointment"
}
```

- `session_id`: Provide existing session UUID to continue conversation, or `null`/`""` to start new

**Response:**

```json
{
  "session_id": "new-or-existing-uuid",
  "action": {
    "message": "Sure! I'll help you book an appointment. May I have your name?",
    "widget_directive": "TEXT_INPUT",
    "field_meta": {
      "name": "name",
      "type": "text",
      "label": "Full Name",
      "placeholder": "Your full name"
    },
    "options": null,
    "is_terminal": false
  }
}
```

---

### 5.2 User Registration

**Create a new user account and organization.**

```http
POST /api/v1/auth/register
```

| Auth | Rate Limit |
|------|-------------|
| None | 5/min |

**Request:**

```json
{
  "email": "admin@company.com",
  "password": "securepassword123",
  "organization_name": "My Business"
}
```

**Response:**

```json
{
  "user_id": "uuid",
  "org_id": "uuid",
  "api_key": "ds_xxx",
  "access_token": "jwt-token",
  "token_type": "bearer"
}
```

---

### 5.3 User Login

**Authenticate and get JWT token.**

```http
POST /api/v1/auth/login
```

| Auth | Rate Limit |
|------|-------------|
| None | 10/min |

**Request:**

```json
{
  "email": "admin@company.com",
  "password": "securepassword123"
}
```

**Response:**

```json
{
  "user_id": "uuid",
  "org_id": "uuid",
  "access_token": "jwt-token",
  "token_type": "bearer"
}
```

---

### 5.4 Dashboard Stats

**Get organization statistics.**

```http
GET /api/v1/dashboard/stats
```

| Auth | Rate Limit |
|------|-------------|
| JWT | 60/min |

**Response:**

```json
{
  "total_bookings": 150,
  "pending_bookings": 5,
  "confirmed_bookings": 145,
  "total_conversations": 230,
  "this_month_bookings": 45,
  "this_month_conversations": 78
}
```

---

### 5.5 Recent Bookings

**List confirmed bookings.**

```http
GET /api/v1/dashboard/recent-bookings
```

| Auth | Rate Limit |
|------|-------------|
| JWT | 60/min |

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| limit | int | 20 | Number of results |
| offset | int | 0 | Pagination offset |

**Response:**

```json
{
  "bookings": [
    {
      "id": "uuid",
      "customer_name": "John Doe",
      "customer_email": "john@example.com",
      "customer_phone": "+1-555-123-4567",
      "slot_time": "2026-03-17T10:00:00Z",
      "created_at": "2026-03-15T14:30:00Z"
    }
  ],
  "total": 150
}
```

---

### 5.6 Conversations

**List chat sessions.**

```http
GET /api/v1/dashboard/conversations
```

| Auth | Rate Limit |
|------|-------------|
| JWT | 60/min |

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| status | string | all | Filter: `all`, `active`, `confirmed` |
| limit | int | 20 | Results per page |
| offset | int | 0 | Pagination |

**Response:**

```json
{
  "sessions": [
    {
      "id": "uuid",
      "user_id": "uuid-or-null",
      "status": "ACTIVE",
      "collected_data": {
        "name": "John Doe",
        "email": "john@example.com"
      },
      "created_at": "2026-03-15T14:00:00Z",
      "updated_at": "2026-03-15T14:30:00Z"
    }
  ],
  "total": 230
}
```

---

### 5.7 Agent Configuration

**Get or update agent settings.**

```http
GET /api/v1/dashboard/agent-config
PUT /api/v1/dashboard/agent-config
```

| Auth | Rate Limit |
|------|-------------|
| JWT | 30/min |

**GET Response:**

```json
{
  "require_otp": true,
  "max_booking_days": 30,
  "business_hours": {
    "start": "09:00",
    "end": "17:00"
  },
  "field_definitions": [
    {
      "name": "name",
      "type": "text",
      "label": "Full Name",
      "required": true
    },
    {
      "name": "email",
      "type": "email",
      "label": "Email Address",
      "required": true
    }
  ]
}
```

**PUT Request:**

```json
{
  "require_otp": true,
  "max_booking_days": 30,
  "field_definitions": [...]
}
```

---

### 5.8 Time Slots (Availability)

**Manage available appointment slots.**

```http
GET    /api/v1/dashboard/availability
POST   /api/v1/dashboard/availability
DELETE /api/v1/dashboard/availability/{slot_id}
```

| Auth | Rate Limit |
|------|-------------|
| JWT | 60/min |

**GET Response:**

```json
{
  "slots": [
    {
      "id": "uuid",
      "start_time": "2026-03-17T10:00:00Z",
      "end_time": "2026-03-17T10:30:00Z",
      "is_booked": false
    }
  ]
}
```

**POST Request:**

```json
{
  "start_time": "2026-03-17T10:00:00Z",
  "end_time": "2026-03-17T10:30:00Z"
}
```

---

### 5.9 OTP Generation

**Send OTP to user email.**

```http
POST /api/v1/auth/security/otp/generate
```

| Auth | Rate Limit |
|------|-------------|
| None | 5/min |

**Request:**

```json
{
  "session_id": "uuid",
  "email": "customer@email.com"
}
```

**Response:**

```json
{
  "message": "OTP sent to customer@email.com",
  "expires_in": 300
}
```

---

### 5.10 OTP Verification

**Verify OTP code.**

```http
POST /api/v1/auth/security/otp/verify
```

| Auth | Rate Limit |
|------|-------------|
| None | 5/min |

**Request:**

```json
{
  "session_id": "uuid",
  "otp_code": "123456"
}
```

**Response:**

```json
{
  "verified": true,
  "message": "OTP verified successfully"
}
```

---

## 6. Widget Directives

The `widget_directive` field tells the frontend what UI to render:

| Directive | Frontend UI |
|-----------|--------------|
| `TEXT_INPUT` | Standard text input |
| `REQUIRE_DATE_PICKER` | Calendar date picker |
| `REQUIRE_TIME_SLOT_GRID` | Grid of time slot buttons |
| `REQUIRE_EMAIL_INPUT` | Email input field |
| `REQUIRE_PHONE_INPUT` | Phone input field |
| `REQUIRE_SELECT` | Dropdown select |
| `REQUIRE_OTP_MODAL` | OTP verification popup |
| `REQUEST_CONFIRMATION` | Booking summary card |
| `BOOKING_CONFIRMED_CARD` | Success message |

---

## 7. Error Codes

| HTTP Code | Meaning |
|-----------|---------|
| 200 | Success |
| 201 | Created |
| 400 | Invalid request data |
| 401 | Not authenticated |
| 403 | Invalid API key or forbidden |
| 404 | Resource not found |
| 422 | Validation error |
| 429 | Rate limited |
| 500 | Server error |

---

## 8. Rate Limits

| Endpoint | Limit |
|----------|-------|
| `/chat/message/dispatch` | 30/min |
| `/auth/register` | 5/min |
| `/auth/login` | 10/min |
| `/auth/otp/*` | 5/min |
| `/dashboard/*` | 60/min |

---

## 9. Related Documents

| Document | What It Covers |
|----------|----------------|
| [System Overview](../architecture/system-overview.md) | High-level architecture |
| [Backend Architecture](../architecture/backend.md) | Implementation details |
| [Booking Flow](../features/booking-flow.md) | How the booking process works |
| [OTP Verification](../features/otp-verification.md) | Security flow |

---

*Level: Intermediate — You should be familiar with REST APIs and JSON.*
