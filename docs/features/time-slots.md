# Time Slots Management

> How to manage appointment availability

This document covers how businesses configure their available booking slots.

---

## 1. Overview

Time slots represent when a business is available for appointments. Each slot has:
- **Start time** — When the appointment begins
- **End time** — When the appointment ends
- **Booked status** — Whether it's taken or available

---

## 2. Slot Structure

```json
{
  "id": "uuid",
  "org_id": "uuid",
  "start_time": "2026-03-17T10:00:00Z",
  "end_time": "2026-03-17T10:30:00Z",
  "is_booked": false
}
```

### Default Slot Duration
- 30 minutes per appointment
- Configurable per organization

---

## 3. Creating Slots

### Via Admin Panel

1. Navigate to **Dashboard → Availability**
2. Click **Add Time Slot**
3. Select date and time
4. Slot appears in the list

### Via API

```http
POST /api/v1/dashboard/availability
Authorization: Bearer <jwt_token>

{
  "start_time": "2026-03-17T10:00:00Z",
  "end_time": "2026-03-17T10:30:00Z"
}
```

---

## 4. Slot Rules

### Booking Constraints

| Rule | Default | Configurable |
|------|---------|---------------|
| Minimum advance booking | Today | Yes |
| Maximum advance booking | 30 days | Yes |
| Slot duration | 30 min | Yes |
| Same-day bookings | Allowed | Yes |

### Business Hours

Slots should be created within configured business hours:

```json
{
  "business_hours": {
    "start": "09:00",
    "end": "17:00"
  }
}
```

---

## 5. Slot Queries

### Fetch Available Slots

When a customer selects a date, the backend queries:

```sql
SELECT * FROM time_slots
WHERE org_id = :org_id
  AND DATE(start_time) = :selected_date
  AND is_booked = false
ORDER BY start_time ASC
```

### Response to Widget

```json
{
  "action": {
    "widget_directive": "REQUIRE_TIME_SLOT_GRID",
    "options": [
      "10:00 AM",
      "10:30 AM",
      "11:00 AM",
      "2:00 PM",
      "2:30 PM"
    ]
  }
}
```

---

## 6. Booking a Slot

### Atomic Transaction

When customer confirms booking:

```python
async def attempt_booking(db, session_id, slot_id, customer_data):
    # 1. Lock the slot row (prevent double-booking)
    slot = await db.execute(
        select(TimeSlot)
        .where(TimeSlot.id == slot_id)
        .with_for_update()  # Blocks other transactions
    )
    
    # 2. Check if still available
    if slot.is_booked:
        raise SlotAlreadyBookedError()
    
    # 3. Create booking
    booking = Booking(
        org_id=session.org_id,
        session_id=session_id,
        slot_id=slot_id,
        customer_name=customer_data["name"],
        customer_email=customer_data["email"],
        customer_phone=customer_data["phone"]
    )
    db.add(booking)
    
    # 4. Mark slot as booked
    slot.is_booked = True
    
    # 5. Commit atomically
    await db.commit()
```

### Why Locking Matters

Without `FOR UPDATE`:
```
Time A: SELECT slot (available) → Customer A books
Time B: SELECT slot (available) → Customer B books
Time C: Both commit → DOUBLE BOOKING!
```

With `FOR UPDATE`:
```
Time A: SELECT slot FOR UPDATE → Locked
Time B: SELECT slot FOR UPDATE → Waits...
Time A: COMMIT → Slot marked booked
Time B: SELECT → Slot booked → Error
```

---

## 7. Managing Slots

### Delete a Slot

```http
DELETE /api/v1/dashboard/availability/{slot_id}
Authorization: Bearer <jwt_token>
```

**Rules:**
- Cannot delete booked slots
- Can only delete future slots

### Bulk Operations

For efficiency, slots can be created in bulk:

```http
POST /api/v1/dashboard/availability/bulk
Authorization: Bearer <jwt_token>

{
  "slots": [
    {"start_time": "2026-03-20T09:00:00Z", "end_time": "2026-03-20T09:30:00Z"},
    {"start_time": "2026-03-20T09:30:00Z", "end_time": "2026-03-20T10:00:00Z"},
    ...
  ]
}
```

---

## 8. Slot Display

### Widget Time Format

Slots are formatted for display:

| 24-hour | 12-hour |
|---------|---------|
| 09:00 | 9:00 AM |
| 13:00 | 1:00 PM |
| 17:30 | 5:30 PM |

### Timezone Considerations

- Slots stored in UTC
- Display converted to customer's local time
- Or business local time (configurable)

---

## 9. Edge Cases

### No Slots Available

```json
{
  "message": "Sorry, no slots available on that date. Would you like to try another day?"
}
```

### All Slots Booked

```json
{
  "message": "That date is fully booked. Here are available dates:\n- March 18\n- March 19\n- March 20"
}
```

### Past Date Selected

Automatically rejected — customers can only book future dates.

---

## 10. Related Documents

| Document | What It Covers |
|----------|----------------|
| [Booking Flow](booking-flow.md) | Complete booking process |
| [API Overview](../api/overview.md) | Availability endpoints |
| [Database Schema](../architecture/database.md) | time_slots table |

---

*Level: Intermediate — Understanding of database transactions helpful.*
