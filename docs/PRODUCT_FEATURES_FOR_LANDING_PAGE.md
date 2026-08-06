# DotStark AI Booking Agent — Product Feature Document
### Source of truth for building landing pages, sales decks & marketing copy
_Last updated: 2026-08-04 · Verified against the codebase (backend, chat-widget, admin-panel)_

> **How to use:** Each product gets its own section with (1) marketing positioning, (2) a verified feature list, (3) page-ready copy snippets, and (4) screenshots to capture. Anything marked ⚠️ is *not fully built* — don't advertise it as-is. The super-admin control plane is a separate internal product and is **excluded** from customer-facing pages.

---

# 🎯 Core Brand & Messaging

| Element | Value |
|---|---|
| **Product name** | DotStark AI Booking Agent |
| **One-liner** | An AI that turns website visitors into confirmed appointments — 24/7. |
| **Category** | AI appointment-scheduling assistant / conversational booking |
| **Business model** | Self-serve SaaS — visitors register at `/register` (email + password → creates account + organization), then embed the widget |
| **Target users** | SMBs & service businesses (salons, clinics, consultants, agencies) + their customers |
| **Tone of voice** | Confident, modern, benefit-first; technical credibility without jargon |

### Tagline options
- *"Your website just learned to schedule."*
- *"Every visitor, booked — even while you sleep."*
- *"From 'just looking' to 'confirmed' in one conversation."*

### Key talk tracks
1. **Revenue after hours** — the AI books appointments 24/7 so you never lose a 9 PM browser.
2. **Zero friction** — visitors chat naturally; no forms, no phone tag.
3. **Never double-booked** — slots are locked atomically the moment a customer confirms.
4. **You stay in control** — a full dashboard + human handoff means the AI is your assistant, not a black box.

---

# 🤖 PRODUCT 1 — The Chat Widget (customer-facing)

The embeddable bubble/panel your customers' visitors interact with. Shadow-DOM isolated single-file JS bundle (~83 KB), loaded with **one `<script>` tag**.

## Verified feature list

| Area | Feature | Status |
|---|---|---|
| **Embedding** | One-script embed on any site (HTML, WordPress, React…) | ✅ |
| | Shadow DOM isolation — zero CSS bleed | ✅ |
| | Custom brand color via `data-color` | ✅ |
| | Auto-detects API base from script `src` or `data-api-base` | ✅ |
| | Widget API-token authentication (`data-widget-token`) | ✅ |
| **Conversation** | Natural-language booking (parses "tomorrow at 3") | ✅ |
| | Real-time availability from your slot calendar | ✅ |
| | Timezone-aware (auto-detects visitor timezone) | ✅ |
| | Human handoff — staff can take over live (WebSocket) | ✅ |
| | Typing indicators (visitor & agent) | ✅ |
| | Session persisted in `sessionStorage` (survives refresh) | ✅ |
| | WhatsApp parity — same AI answers on WhatsApp (Meta Cloud API) | ✅ |
| **Booking integrity** | Atomic slot locking — no double-bookings | ✅ |
| | Email OTP verification before confirming | ✅ |
| | Configurable fields to collect (name/email/phone/notes) | ✅ |
| **Intelligence** | RAG knowledge base — upload PDFs or auto-crawl your site; AI answers questions accurately | ✅ |
| | Tone/personality configurable from dashboard | ✅ |
| **Security** | Rate-limited public endpoints | ✅ |
| | CSP-friendly, no third-party cookies | ✅ |

### ⚠️ NOT currently in the widget (don't advertise)
- ❌ Voice input (Web Speech API mic button)
- ❌ File/attachment upload in chat
- ❌ Emoji picker
- ❌ Thumbs-up/down feedback UI on messages (exists only in backend/dash)
- ❌ Dark-mode variant of the widget (admin panel *has* a theme toggle; widget does not)

## Page-ready copy — Widget

> ### Talk is cheap. This one books.
> Embed a smart assistant that chats with your visitors, answers questions about your business, and locks in real appointments — all night, every night.

**3-step "how it works":**
1. **Paste one line.** Drop a single script tag on your site and pick your brand color.
2. **Visitors just chat.** "Can I come in Friday afternoon?" — the AI finds a real open slot.
3. **You get a confirmed booking.** Slot locked instantly, OTP-verified, in your calendar.

**Conversation mock (visual section):**
> **Visitor:** Do you have anything this weekend?
> **Agent:** Saturday has **10:00 AM** and **2:30 PM** open. Which works?
> **Visitor:** 2:30 🙌
> **Agent:** Great — I've sent a code to your email to confirm.
> **Visitor:** 481-209 ✅
> **Agent:** You're booked for Saturday at 2:30 PM. See you then!

**Screenshots to capture:** floating bubble closed · expanded panel mid-chat · quick-date picker · OTP entry · "human agent joined" takeover notice · mobile view.

---

# 📊 PRODUCT 2 — The Admin Panel (business-facing dashboard)

Next.js 15 / React 19 dashboard. Six main sections — perfect for alternating feature blocks.

## Section-by-section (verified)

### 1️⃣ Dashboard — *"Your day at a glance"*
- Bookings stats & conversation volume
- System alerts surface here first
- Quick access to recent activity

### 2️⃣ Calendar — *"The schedule that manages itself"*
- **Agenda view** of all bookings (list + color-coded)
- Create / edit / cancel bookings manually (walk-ins, phone bookings) via dialog
- Busy-slot visualization synced from connected calendars

### 3️⃣ Inbox — *"Human-in-the-loop, built in"*
- Real-time feed of live AI conversations (WebSocket)
- **One-click human takeover** — reply as yourself in the same thread; hand back to the AI when done
- Typing indicators, message history, session resolve
- In-app toast notifications when a visitor requests a human

### 4️⃣ Agent Personalisation — *"Teach it your business"*
- Configure AI **tone**, **greeting**, and the **fields to collect** during booking (name, email, phone, custom)
- **Knowledge Base tab:**
  - Drag-and-drop **PDF upload**
  - **Crawl your own website** (Firecrawl) with one click
  - Indexed by the RAG engine so the AI answers questions about *your* services

### 5️⃣ Business Hours — *"You set the rules, the AI enforces them"*
- Weekly schedule + per-day overrides
- Holidays & one-off exceptions
- Slot duration & buffer times
- Max advance booking window, per-slot capacity
- Timezone-aware — customers see times in *their* timezone

### 6️⃣ Integrations — *"Plays nicely with your stack"*
- **Google Calendar** two-way sync
- **Outlook Calendar** two-way sync
- **WhatsApp** (Meta WhatsApp Cloud API — verified by `graph.facebook.com` client)
- **SMTP email** with delivery tracking
- **Widget embed-code generator** + API keys with rotation

## Cross-cutting admin capabilities
- Self-serve **registration** (`/register` → creates account + organization automatically)
- **Multi-user** per organization
- **Audit log** of key actions (who did what, when — hash-chained, tamper-evident) — surfaced via API & super-admin; tenant-side "activity history" is exposed via conversations dashboard
- **Dark / light theme toggle** in the dashboard
- **Multi-tenant architecture** — one deployment can serve 100+ businesses, isolated

### ⚠️ NOT available to tenants (belongs to a separate super-admin product)
- Stripe billing/invoicing/refund UI
- SLA alerting, analytics reports, feature flags — these live in the platform *operator* console, not the business dashboard

## Page-ready copy — Admin Panel

> ### Mission control for every booking.
> Run your AI agent, your calendar, and your live conversations from one clean dashboard. Jump in when a human touch matters; let the AI handle the rest.

**Feature block snippets:**
- **Calendar:** *"See every appointment at a glance — and add the phone-call bookings yourself in seconds."*
- **Inbox:** *"The moment a visitor needs a human, you get a ping. Take over with one click, hand back when you're done."*
- **Agent Personalisation:** *"Upload a PDF or paste your URL — your AI learns your business in minutes."*
- **Business Hours:** *"Set your availability once. Your AI never books outside it, in any timezone."*
- **Integrations:** *"Google, Outlook, WhatsApp, email — connected in clicks, not code."*

**Screenshots to capture:** dashboard home w/ stats · calendar/agenda view · inbox thread w/ takeover button · knowledge-base upload + crawl UI · business-hours weekly editor · integrations grid · theme toggle dark mode.

---

# 📐 Suggested page structure (both pages)

1. **Hero** — headline + sub + CTA + product screenshot
2. **Social-proof / trust bar** (add real numbers once known)
3. **How it works** (3 steps, icons)
4. **Feature sections** — alternating image/text, one per dashboard tab or widget capability
5. **Conversation demo** — styled chat mock
6. **Integrations strip** — logos (Google, Outlook, WhatsApp, SMTP)
7. **Security/Trust section** — OTP, rate limiting, audit logging, no-double-booking guarantee
8. **Final CTA** — free self-serve signup

---

# 🚫 Marketing guardrails (important)

| ❌ Don't claim yet | ✅ Safe to claim |
|---|---|
| Voice input | Natural-language chat |
| File uploads in chat | PDF/website knowledge base |
| Message reaction feedback | Typing indicators |
| Widget dark mode | Dashboard dark/light theme |
| Stripe billing for tenants | (Stripe exists only in internal super-admin) |
| Multi-language support | Not verified — omit |
| Any specific performance stats ("30 seconds", "99.9 %") | Only after measuring |

**Rule of thumb:** everything in the ✅ tables above is in the code *today*; anything with ⚠️ or ❌ is either unfinished or lives in the separate super-admin product.

---

# 📎 SEO keyword bank
`AI appointment booking chatbot` · `embeddable booking widget` · `AI scheduling assistant for small business` · `WhatsApp booking bot` · `Google/Outlook calendar sync` · `24/7 online appointment scheduling`
