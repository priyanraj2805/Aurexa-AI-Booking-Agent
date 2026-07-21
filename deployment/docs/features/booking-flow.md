# Booking Flow

> Step-by-step guide to how a customer books an appointment

This document explains the complete booking journey from the customer's perspective and the technical flow behind it.

---

## 1. Customer Journey

### Visual Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        CUSTOMER BOOKING JOURNEY                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  1. CUSTOMER OPENS WIDGET                                          │    │
│  │     - Sees: "Hi! How can I help you today?"                        │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  2. CUSTOMER SAYS: "I want to book an appointment"               │    │
│  │     - Bot asks: "May I have your full name?"                      │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  3. CUSTOMER PROVIDES NAME                                         │    │
│  │     - Bot asks: "What's your email address?"                      │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  4. CUSTOMER PROVIDES EMAIL                                        │    │
│  │     - Bot asks: "What's your phone number?"                        │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  5. CUSTOMER PROVIDES PHONE                                       │    │
│  │     - Bot shows date picker: "Select a date"                      │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  6. CUSTOMER SELECTS DATE                                         │    │
│  │     - Bot shows time slots: "Choose a time"                       │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  7. CUSTOMER SELECTS TIME SLOT                                    │    │
│  │     - Bot shows summary + "Confirm?"                               │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  8. OTP VERIFICATION (if enabled)                                │    │
│  │     - Bot: "Enter code sent to your email"                        │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                    │                                         │
│                                    ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │  9. CUSTOMER CONFIRMS                                            │    │
│  │     - Bot: "Booking confirmed! Reference: #ABC123"               │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Field Collection Order

The order of fields is configurable per organization:

### Default Order

1. **name** — Full name
2. **email** — Email address
3. **phone** — Phone number
4. **date** — Preferred date
5. **time** — Preferred time slot

### Custom Fields

Organizations can define custom fields:

```json
[
  {"name": "service", "type": "select", "label": "Service Type"},
  {"name": "notes", "type": "textarea", "label": "Special Requests"}
]
```

---

## 3. Technical Flow

### Step 1: Start Conversation

```http
POST /api/v1/chat/message/dispatch
```

```json
{
  "session_id": null,
  "message": "I want to book an appointment"
}
```

**Backend Processing:**
1. Creates new `chat_sessions` record
2. LLM identifies `START_BOOKING` intent
3. StateEvaluator returns first missing field: `name`
4. Returns `TEXT_INPUT` directive

**Response:**

```json
{
  "session_id": "uuid-new-session",
  "action": {
    "message": "I'd be happy to help you book an appointment. May I have your full name?",
    "widget_directive": "TEXT_INPUT",
    "field_meta": {
      "name": "name",
      "type": "text",
      "label": "Full Name"
    }
  }
}
```

---

### Step 2-5: Collect Each Field

For each user response:

```http
POST /api/v1/chat/message/dispatch
```

```json
{
  "session_id": "existing-uuid",
  "message": "John Doe"
}
```

**Backend Processing:**
1. LLM extracts: `{name: "John Doe"}`
2. DataValidator validates format
3. Session updated: `collected_data.name = "John Doe"`
4. StateEvaluator updates `missing_fields`
5. Returns next required field (e.g., `email`)

**Response:**

```json
{
  "session_id": "existing-uuid",
  "action": {
    "message": "Thanks, John! What's your email address?",
    "widget_directive": "REQUIRE_EMAIL_INPUT",
    "field_meta": {
      "name": "email",
      "type": "email"
    }
  }
}
```

---

### Step 6: Date Selection

When date field is required:

```json
{
  "session_id": "existing-uuid",
  "action": {
    "message": "What date would you like to book?",
    "widget_directive": "REQUIRE_DATE_PICKER",
    "field_meta": {
      "name": "date",
      "type": "date"
    }
  }
}
```

**Frontend:** Renders calendar picker

**User selects date:** `2026-03-17`

**Backend validates:**
- Date is in the future
- Date is within `max_booking_days` limit
- Date has available slots

---

### Step 7: Time Slot Selection

After date is collected:

```json
{
  "session_id": "existing-uuid",
  "action": {
    "message": "Great! What time works for you?",
    "widget_directive": "REQUIRE_TIME_SLOT_GRID",
    "field_meta": {
      "name": "time",
      "type": "time"
    },
    "options": ["10:00 AM", "1:00 PM", "2:00 PM", "4:00 PM", "5:00 PM"]
  }
}
```

**How time slots are fetched:**
1. Backend queries `time_slots` table
2. Filters by: `org_id`, `date`, `is_booked = false`
3. Returns available times as `options` array

**Frontend:** Renders clickable button grid

---

### Step 8: Confirmation Summary

When all fields collected:

```json
{
  "session_id": "existing-uuid",
  "action": {
    "message": "Here's your booking:\n\n📅 Date: March 17, 2026\n👤 Name: John Doe\n📧 Email: john@example.com\n📞 Phone: +1-555-123-4567\n⏰ Time: 10:00 AM\n\nShall I confirm this booking?",
    "widget_directive": "REQUEST_CONFIRMATION"
  }
}
```

---

### Step 9: OTP Verification (Optional)

If `require_otp` is enabled:

```json
{
  "session_id": "existing-uuid",
  "action": {
    "message": "To confirm, please enter the code sent to john@example.com",
    "widget_directive": "REQUIRE_OTP_MODAL"
  }
}
```

**Flow:**
1. Backend generates 6-digit OTP
2. Sends via SMTP to customer's email
3. User enters OTP
4. Backend verifies: `otp_verified = true`

---

### Step 10: Final Confirmation

User says "yes" or "confirm":

**Backend Processing:**
1. Updates session status to `CONFIRMING`
2. Gets time slot from `collected_data.time`
3. **Atomic booking:**
   ```sql
   BEGIN;
   SELECT * FROM time_slots WHERE id = :slot_id FOR UPDATE;
   INSERT INTO bookings ...;
   UPDATE time_slots SET is_booked = true WHERE id = :slot_id;
   COMMIT;
   ```
4. Updates session status to `CONFIRMED`
5. Sends confirmation email to customer
6. Sends notification to business

**Response:**

```json
{
  "session_id": "existing-uuid",
  "action": {
    "message": "✅ Booking Confirmed!\n\nReference: #ABC123\n📅 March 17, 2026 at 10:00 AM\n\nA confirmation email has been sent to john@example.com",
    "widget_directive": "BOOKING_CONFIRMED_CARD"
  }
}
```

---

## 4. Error Handling

### Invalid Input

```json
{
  "message": "I couldn't understand that. Please enter a valid email (e.g., name@example.com)"
}
```

### No Available Slots

```json
{
  "message": "Sorry, no slots available on March 17. Would you like to try another date?"
}
```

### Slot Already Booked

```json
{
  "message": "That slot was just taken. Please select another time."
}
```

### OTP Failed 5 Times

```json
{
  "message": "Too many attempts. Please try again in 15 minutes."
}
```

---

## 5. Session State

Throughout the flow, session state is stored:

```json
{
  "id": "session-uuid",
  "session_status": "ACTIVE",
  "collected_data": {
    "name": "John Doe",
    "email": "john@example.com",
    "phone": "+1-555-123-4567",
    "date": "2026-03-17",
    "time": "10:00 AM"
  },
  "missing_fields": [],
  "current_intent": "CONFIRM_BOOKING",
  "otp_verified": true
}
```

---

## 6. Related Documents

| Document | What It Covers |
|----------|----------------|
| [API Overview](../api/overview.md) | All endpoints |
| [Time Slots](time-slots.md) | Availability management |
| [OTP Verification](otp-verification.md) | Security flow |
| [Backend Architecture](../architecture/backend.md) | Implementation details |

---

*Level: Beginner to Intermediate — This is the core feature of the platform.*
