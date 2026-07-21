# Frontend Architecture

> Breakdown of the Admin Panel and Chat Widget

The frontend consists of two separate applications:
1. **Admin Panel** — Next.js dashboard for businesses
2. **Chat Widget** — Embeddable web component for customers

---

## 1. Project Overview

```
frontend/
├── admin-panel/           # Next.js 14 + shadcn/ui
│   ├── src/app/         # App router pages
│   ├── src/components/ # Reusable components
│   ├── src/hooks/      # Custom React hooks
│   └── src/lib/        # API client, utilities
│
└── chat-widget/         # Vanilla JS Web Component
    ├── src/
    │   ├── index.js    # Main widget code
    │   ├── styles.css  # Widget styles
    │   └── config.js   # Configuration
    └── index.html      # Demo page
```

---

## 2. Admin Panel (Next.js)

### Tech Stack

| Technology | Purpose |
|------------|---------|
| Next.js 14 | Framework (App Router) |
| React 18 | UI Library |
| shadcn/ui | Component library |
| Tailwind CSS | Styling |
| TypeScript | Type safety |
| React Query | Server state |

### Pages Structure

```
admin-panel/src/app/
├── page.tsx                    # Landing/Login
├── (auth)/
│   └── login/
│       └── page.tsx            # Login form
│
└── dashboard/
    ├── layout.tsx             # Dashboard layout + sidebar
    ├── page.tsx               # Stats overview
    │
    ├── bookings/
    │   └── page.tsx           # Booking list
    │
    ├── conversations/
    │   └── page.tsx           # Chat sessions
    │
    ├── availability/
    │   └── page.tsx           # Time slot management
    │
    ├── integrations/
    │   └── page.tsx           # Widget embed code
    │
    └── settings/
        └── page.tsx           # Agent configuration
```

### Key Components

| Component | Purpose |
|-----------|---------|
| `AppSidebar` | Navigation sidebar |
| `StatsCard` | Dashboard stat displays |
| `BookingTable` | Paginated booking list |
| `ConversationList` | Chat session history |
| `SlotManager` | Time slot CRUD interface |
| `WidgetPreview` | Live widget preview |
| `AgentConfigForm` | Field definitions editor |

### Authentication Flow

```
1. User visits /login
2. Enters email + password
3. POST /api/v1/auth/login
4. Receive JWT token
5. Store in localStorage
6. Redirect to /dashboard
7. All future requests: Authorization: Bearer <token>
```

### API Client (`lib/api.ts`)

```typescript
// Base API client with JWT auth
export const api = {
  async get<T>(url: string): Promise<T> {
    const token = localStorage.getItem('token');
    const response = await fetch(url, {
      headers: {
        'Authorization': `Bearer ${token}`,
      },
    });
    return response.json();
  },
  
  async post<T>(url: string, data: any): Promise<T> {
    // ... similar with POST
  },
};
```

---

## 3. Chat Widget (Vanilla JS)

### What Is It?

A **Web Component** — a custom HTML element that works like native HTML tags.

```html
<!-- Embed code -->
<dotstark-booking-widget 
  api-key="ds_xxx"
  api-url="https://your-backend.com"
></dotstark-booking-widget>

<script src="https://cdn.yoursite.com/widget.js"></script>
```

### Key Features

- **Zero Dependencies** — Plain JavaScript, no React/Vue/Angular
- **Shadow DOM** — Styles isolated from host page
- **Responsive** — Works on mobile and desktop
- **Accessible** — Keyboard navigation, ARIA labels
- **Customizable** — Colors via CSS variables

### Widget Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    <dotstark-booking-widget>                 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                   HEADER (toggle)                     │  │
│  └─────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                   MESSAGE LIST                       │  │
│  │  ┌─────────┐                                         │  │
│  │  │ Agent   │ Hi! How can I help?                     │  │
│  │  └─────────┘                                         │  │
│  │  ┌─────────┐                                         │  │
│  │  │ User    │ I want to book an appointment           │  │
│  │  └─────────┘                                         │  │
│  └─────────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                   INPUT AREA                         │  │
│  │  ┌─────────────────────────────────┐ ┌───────────┐  │  │
│  │  │ Type a message...               │ │  Send    │  │  │
│  │  └─────────────────────────────────┘ └───────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐  │
│  │            WIDGET COMPONENTS (when needed)          │  │
│  │  - Date Picker (calendar popup)                     │  │
│  │  - Time Slot Grid (button grid)                     │  │
│  │  - OTP Modal (verification input)                   │  │
│  │  - Summary Card (booking review)                     │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Widget Directives

The backend tells the widget what UI to show via `widget_directive`:

| Directive | UI Rendered |
|-----------|--------------|
| `TEXT_INPUT` | Default text input |
| `REQUIRE_DATE_PICKER` | Calendar date picker |
| `REQUIRE_TIME_SLOT_GRID` | Grid of time buttons |
| `REQUIRE_OTP_MODAL` | OTP verification popup |
| `REQUEST_CONFIRMATION` | Booking summary card |
| `BOOKING_CONFIRMED_CARD` | Success message |

### Widget State Machine

```
┌─────────────────────────────────────────────────────────────────┐
│                      WIDGET STATE FLOW                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌──────────┐                                                  │
│   │  IDLE    │◄──────────────────────────────────────────────┐  │
│   │(closed)  │                                               │  │
│   └────┬─────┘                                               │  │
│        │ click                                              │  │
│        ▼                                                     │  │
│   ┌──────────┐     send message      ┌──────────────────┐   │  │
│   │  OPEN    │ ─────────────────────▶│   THINKING...    │   │  │
│   │(chatting)│                       └────────┬─────────┘   │  │
│   └──────────┘                                │              │  │
│        │                                      │ response     │  │
│        │                                      ▼              │  │
│        │                              ┌──────────────────┐   │  │
│        │                              │   RENDER_UI     │   │  │
│        │                              │  (directive)     │   │  │
│        │                              └────────┬─────────┘   │  │
│        │                                       │              │  │
│        │                                       │ user input  │  │
│        │◀──────────────────────────────────────┘              │  │
│        │                                                       │  │
│        │ close                                                 │  │
│        └───────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Embed Code Generation

The Admin Panel generates embed code for each organization:

```html
<!-- Generated in Admin Panel → Integrations page -->
<script 
  src="https://cdn.yourdomain.com/widget.js"
  async
></script>
<dotstark-booking-widget 
  api-key="ds_vZZajXyBCoXNbzl1op0pouNW5GiWx3cA"
  api-url="https://api.yourdomain.com"
></dotstark-booking-widget>
```

---

## 4. Frontend → Backend Communication

### Chat Widget Flow

```javascript
// widget sends message
async function sendMessage(message) {
  const response = await fetch(`${API_URL}/api/v1/chat/message/dispatch`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Org-Api-Key': API_KEY,  // Organization API key
    },
    body: JSON.stringify({
      session_id: currentSessionId,  // UUID
      message: message,
    }),
  });
  
  return response.json();
}
```

### Response Format

```json
{
  "session_id": "uuid-of-session",
  "action": {
    "message": "Sure, what's your name?",
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

## 5. Running the Frontends

### Admin Panel

```bash
cd frontend/admin-panel
npm install
npm run dev
# Opens at http://localhost:3002
```

### Chat Widget (Development)

```bash
cd frontend/chat-widget
npm install
npm run dev
# Opens demo page at http://localhost:5174
```

---

## 6. Building for Production

### Admin Panel

```bash
cd frontend/admin-panel
npm run build
# Output: .next/ folder
```

### Chat Widget

```bash
cd frontend/chat-widget
npm run build
# Output: dist/widget.js
```

---

## 7. Related Documents

| Document | What It Covers |
|----------|----------------|
| [System Overview](system-overview.md) | High-level architecture |
| [Backend Architecture](backend.md) | FastAPI backend |
| [Database Schema](database.md) | Data models |
| [API Overview](../api/overview.md) | Endpoint documentation |

---

*Level: Intermediate — You should be familiar with React fundamentals and basic JavaScript concepts.*
