# Database Architecture

> PostgreSQL schema design and relationships

This document covers the database tables, their purpose, and how they relate to each other.

---

## 1. Schema Overview

```
┌──────────────────┐       ┌──────────────────┐
│  organizations   │       │      users       │
├──────────────────┤       ├──────────────────┤
│ id (PK)          │◄──────│ org_id (FK)      │
│ name             │       │ id (PK)          │
│ api_key          │       │ email            │
│ is_active        │       │ password_hash    │
│ created_at       │       │ role            │
└──────────────────┘       └──────────────────┘
         │                         │
         │                         │
         ▼                         ▼
┌──────────────────────────────────────────────────────┐
│                  chat_sessions                       │
├──────────────────────────────────────────────────────┤
│ id (PK)                                             │
│ org_id (FK) ──────────► organizations.id            │
│ user_id (FK) ─────────► users.id (nullable)        │
│ session_status (ACTIVE/CONFIRMING/CONFIRMED)       │
│ collected_data (JSONB)                             │
│ missing_fields (JSONB)                              │
│ current_intent (nullable)                           │
│ otp_verified (boolean)                              │
│ created_at, updated_at                             │
└──────────────────────────────────────────────────────┘
         │
         │
         ▼
┌──────────────────┐       ┌──────────────────┐
│    bookings      │       │   time_slots     │
├──────────────────┤       ├──────────────────┤
│ id (PK)          │       │ id (PK)          │
│ org_id (FK)     │       │ org_id (FK)      │
│ session_id (FK) │       │ start_time       │
│ slot_id (FK) ──────────►│ end_time         │
│ customer_name   │       │ is_booked        │
│ customer_email  │       └──────────────────┘
│ customer_phone  │
│ created_at      │
└──────────────────┘

┌──────────────────┐
│  chat_messages  │
├──────────────────┤
│ id (PK)          │
│ session_id (FK)  │
│ role (user/agent)│
│ content          │
│ created_at       │
└──────────────────┘
```

---

## 2. Tables

### 2.1 organizations

Each business/tenant has one organization record.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| name | VARCHAR(255) | Organization name |
| api_key | VARCHAR(64) | Unique API key (prefix: `ds_`) |
| is_active | BOOLEAN | Whether org is active |
| created_at | TIMESTAMP | Creation timestamp |

**Indexes:**
- `idx_org_api_key` on `api_key`

### 2.2 users

Admin users who manage the organization.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| org_id | UUID | FK → organizations.id |
| email | VARCHAR(255) | User email (unique per org) |
| password_hash | VARCHAR(255) | Bcrypt hash |
| role | VARCHAR(50) | `admin`, `manager`, etc. |
| created_at | TIMESTAMP | Creation timestamp |

**Indexes:**
- `idx_user_org` on `(org_id, email)`

### 2.3 chat_sessions

Conversation state — stores all booking data as JSON.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| org_id | UUID | FK → organizations.id |
| user_id | UUID | FK → users.id (nullable) |
| session_status | VARCHAR(20) | `ACTIVE`, `CONFIRMING`, `CONFIRMED` |
| collected_data | JSONB | Fields collected: `{name, email, phone, date, time}` |
| missing_fields | JSONB | Fields still needed: `["time"]` |
| current_intent | VARCHAR(50) | Current intent: `START_BOOKING`, etc. |
| otp_verified | BOOLEAN | OTP confirmed |
| created_at | TIMESTAMP | Session start |
| updated_at | TIMESTAMP | Last activity |

**Indexes:**
- `idx_session_org` on `org_id`
- `idx_session_status` on `session_status`

**Example `collected_data`:**
```json
{
  "name": "John Doe",
  "email": "john@example.com",
  "phone": "+1-555-123-4567",
  "date": "2026-03-17",
  "time": "10:00 AM"
}
```

### 2.4 time_slots

Available appointment slots for each organization.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| org_id | UUID | FK → organizations.id |
| start_time | TIMESTAMPTZ | Slot start (UTC) |
| end_time | TIMESTAMPTZ | Slot end (UTC) |
| is_booked | BOOLEAN | Whether slot is taken |

**Indexes:**
- `ix_timeslot_availability` on `(org_id, is_booked, start_time)`
- Unique constraint: `(org_id, start_time)`

### 2.5 bookings

Confirmed appointments.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| org_id | UUID | FK → organizations.id |
| session_id | UUID | FK → chat_sessions.id |
| slot_id | UUID | FK → time_slots.id |
| customer_name | VARCHAR(255) | Customer name |
| customer_email | VARCHAR(255) | Customer email |
| customer_phone | VARCHAR(50) | Customer phone |
| created_at | TIMESTAMP | Booking creation |

**Indexes:**
- `idx_booking_org` on `org_id`
- `idx_booking_slot` on `slot_id` (unique)

### 2.6 chat_messages

Transcript of each conversation.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| session_id | UUID | FK → chat_sessions.id |
| role | VARCHAR(10) | `user` or `agent` |
| content | TEXT | Message content |
| created_at | TIMESTAMP | Message timestamp |

**Indexes:**
- `idx_message_session` on `session_id`

### 2.7 org_configs

Organization-specific settings.

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | Primary key |
| org_id | UUID | FK → organizations.id (unique) |
| config | JSONB | Settings: `{require_otp, max_booking_days, ...}` |
| field_definitions | JSONB | Custom field config |
| created_at | TIMESTAMP | Creation timestamp |
| updated_at | TIMESTAMP | Last update |

---

## 3. Key Design Patterns

### 3.1 Multi-Tenancy

Every table has `org_id` column for isolation:

```python
# Always scope queries
result = await db.execute(
    select(Booking).where(Booking.org_id == current_org_id)
)
```

### 3.2 JSONB for Flexibility

`chat_sessions.collected_data` and `org_configs` use JSONB:

**Advantages:**
- Add new fields without migrations
- Store variable field definitions
- Fast queries with JSON operators

**Query example:**
```sql
SELECT * FROM chat_sessions 
WHERE collected_data->>'email' = 'john@example.com';
```

### 3.3 Atomic Booking

Using `SELECT FOR UPDATE` to prevent double-booking:

```python
# In crud/booking.py
async def attempt_booking(db, session_id, slot_id, customer_data):
    # Lock the slot row
    slot = await db.execute(
        select(TimeSlot)
        .where(TimeSlot.id == slot_id)
        .with_for_update()
    )
    
    if slot.is_booked:
        raise SlotAlreadyBookedError()
    
    # Create booking
    booking = Booking(...)
    db.add(booking)
    
    # Mark slot as booked
    slot.is_booked = True
    
    await db.commit()
```

---

## 4. Migrations

Migrations are managed with Alembic.

```bash
# Create new migration
alembic revision --autogenerate -m "add new column"

# Run migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

### Migration Files

Located in `backend/alembic/versions/`:
- `5a303441c02a_initial_tables.py` - Initial schema
- `363bb6d0be9e_add_session_and_bookings_tables.py` - Sessions & bookings
- `fea6fd19bee6_add_field_definitions_column.py` - JSONB fields
- And more...

---

## 5. Related Documents

| Document | What It Covers |
|----------|----------------|
| [System Overview](system-overview.md) | High-level architecture |
| [Backend Architecture](backend.md) | FastAPI backend |
| [Frontend Architecture](frontend.md) | Admin panel & widget |
| [API Overview](../api/overview.md) | Endpoint documentation |

---

*Level: Intermediate — You should be familiar with SQL, PostgreSQL, and ORM concepts.*
