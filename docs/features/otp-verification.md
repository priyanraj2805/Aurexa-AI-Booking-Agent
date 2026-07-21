# OTP Verification

> Email-based booking confirmation

This document explains the One-Time Password (OTP) verification flow that adds security to bookings.

---

## 1. Overview

OTP verification ensures that:
- The customer owns the email address provided
- Booking confirmations go to the correct inbox
- Reduces fake/spam bookings

---

## 2. How It Works

### Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         OTP VERIFICATION FLOW                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. Customer completes all booking fields                                   │
│                                                                              │
│  2. Customer clicks "Confirm Booking"                                       │
│                                                                              │
│     ┌────────────────────────────────────────────────────────────────┐      │
│     │  3. Backend generates 6-digit OTP                             │      │
│     │     - Random number: 123456                                    │      │
│     │     - Hashes and stores in otp_records table                 │      │
│     │     - Sets expiry: 5 minutes                                  │      │
│     └────────────────────────────────────────────────────────────────┘      │
│                                    │                                         │
│                                    ▼                                         │
│     ┌────────────────────────────────────────────────────────────────┐      │
│     │  4. Backend sends email                                        │      │
│     │     - To: customer@email.com                                   │      │
│     │     - Subject: Your verification code                          │      │
│     │     - Body: Your code is 123456 (expires in 5 minutes)        │      │
│     └────────────────────────────────────────────────────────────────┘      │
│                                    │                                         │
│                                    ▼                                         │
│  5. Widget shows OTP input modal                                           │
│                                                                              │
│     ┌────────────────────────────────────────────────────────────────┐      │
│     │  6. Customer enters: 123456                                   │      │
│     │     - Backend verifies against stored hash                    │      │
│     │     - If correct: otp_verified = true                         │      │
│     └────────────────────────────────────────────────────────────────┘      │
│                                    │                                         │
│                                    ▼                                         │
│  7. Booking confirmed                                                      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Configuration

### Enable/Disable OTP

In **Admin Panel → Settings**:

```json
{
  "require_otp": true
}
```

When disabled, bookings are confirmed immediately without OTP.

---

## 4. OTP Generation

### API Endpoint

```http
POST /api/v1/auth/security/otp/generate
Content-Type: application/json

{
  "session_id": "uuid-session",
  "email": "customer@email.com"
}
```

### Response

```json
{
  "message": "OTP sent to customer@email.com",
  "expires_in": 300
}
```

### Email Template

```
Subject: Your DotStark Booking Verification Code

Hi,

Your verification code is: 123456

This code expires in 5 minutes.

Enter this code in the booking widget to confirm your appointment.

Thanks,
Your Business Name
```

---

## 5. OTP Verification

### API Endpoint

```http
POST /api/v1/auth/security/otp/verify
Content-Type: application/json

{
  "session_id": "uuid-session",
  "otp_code": "123456"
}
```

### Success Response

```json
{
  "verified": true,
  "message": "OTP verified successfully"
}
```

### Error Responses

| Error | Response |
|-------|----------|
| Wrong code | `{"detail": "Invalid verification code"}` |
| Expired | `{"detail": "Verification code expired"}` |
| Max attempts | `{"detail": "Too many attempts. Try again later."}` |

---

## 6. Security Measures

### Rate Limiting

| Action | Limit |
|--------|-------|
| Generate OTP | 5 per minute |
| Verify OTP | 5 per minute |

### Brute-Force Protection

```
Max 5 verification attempts per session
After 5 failures: session locked for 15 minutes
```

### OTP Storage

OTPs are:
- Hashed using bcrypt before storage
- Never stored in plain text
- Single-use (deleted after successful verification)

---

## 7. Session State

After successful verification:

```json
{
  "session_id": "uuid",
  "session_status": "ACTIVE",
  "otp_verified": true,
  "collected_data": {
    "name": "John Doe",
    "email": "john@example.com",
    ...
  }
}
```

---

## 8. Booking After OTP

Once `otp_verified = true`:

1. Customer is prompted to confirm booking
2. Backend processes booking (atomic transaction)
3. Confirmation email sent
4. Widget shows success message

---

## 9. Timeout Handling

### OTP Expiry

- **Duration:** 5 minutes (300 seconds)
- **After expiry:**
  - Customer must request new OTP
  - Previous OTP becomes invalid

### Session Timeout

If customer takes too long:
- Session may expire (configurable)
- OTP verification would need to be redone

---

## 10. Related Documents

| Document | What It Covers |
|----------|----------------|
| [Booking Flow](booking-flow.md) | Complete booking process |
| [API Overview](../api/overview.md) | OTP endpoints |
| [Security Model](../architecture/security.md) | Security details |

---

*Level: Intermediate — Basic understanding of authentication flows.*
