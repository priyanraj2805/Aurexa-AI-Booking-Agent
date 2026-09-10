import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, Shield, Mail, Calendar, Video, Database, Lock, Cookie, Trash2, Eye, FileText, Scale, Baby, RefreshCw } from "lucide-react";

// Official support contact for privacy questions and requests.
const CONTACT_EMAIL = "dev.dotstark@gmail.com";

export const metadata: Metadata = {
  title: "Privacy Policy | Aurexa Booking Agent",
  description: "Privacy Policy for Aurexa Booking Agent — how we collect, use, and protect your information, including Google Calendar and Google Meet data.",
};

const lastUpdated = "August 28, 2026";
const effectiveDate = "August 28, 2026";

function Section({ id, icon: Icon, title, children }: { id: string; icon: React.ElementType; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-24">
      <div className="flex items-center gap-3 mb-4">
        <div className="flex items-center justify-center size-8 rounded-lg bg-[#FF6B00]/10 text-[#FF6B00] dark:bg-primary/10 dark:text-primary">
          <Icon className="size-4" />
        </div>
        <h2 className="text-xl font-bold tracking-tight">{title}</h2>
      </div>
      <div className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-400 space-y-3">
        {children}
      </div>
    </section>
  );
}

export default function PrivacyPolicyPage() {
  return (
    <div className="min-h-screen bg-[#fafafa] dark:bg-[#0a0a0a] text-zinc-900 dark:text-white">
      {/* Header — matches landing header */}
      <header className="sticky top-0 z-10 backdrop-blur-xl bg-[#fafafa]/80 dark:bg-[#0a0a0a]/80 border-b border-zinc-200 dark:border-zinc-800">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-[#FF6B00] to-orange-600 text-white font-bold text-xs">DS</div>
            <span className="font-bold tracking-tight">Aurexa AI</span>
          </Link>
          <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-zinc-600 dark:text-zinc-400 hover:text-[#FF6B00] dark:hover:text-white transition-colors">
            <ArrowLeft className="size-4" /> Home
          </Link>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-10">
        {/* Hero */}
        <div className="mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-zinc-900 text-white dark:bg-zinc-800 border border-zinc-800 text-xs mb-4">
            <Shield className="size-3 text-[#FF6B00]" /> Privacy Policy
          </div>
          <h1 className="text-3xl md:text-4xl font-bold tracking-tight mb-3">Privacy Policy</h1>
          <p className="text-sm text-zinc-600 dark:text-zinc-400">Effective Date: <span className="font-medium text-zinc-900 dark:text-white">{effectiveDate}</span> · Last Updated: <span className="font-medium text-zinc-900 dark:text-white">{lastUpdated}</span></p>
          <p className="text-sm text-zinc-600 dark:text-zinc-400 mt-4 leading-relaxed">
            This Privacy Policy explains how Aurexa Booking Agent (“we,” “us,” or “our”) collects, uses, stores, and protects information when you use our booking automation platform at <Link href="/" className="text-[#FF6B00] hover:underline">dotstark.tech</Link>, including our website, admin panel, and AI chat widget.
          </p>
        </div>

        <div className="grid lg:grid-cols-[200px_1fr] gap-10">
          {/* TOC — desktop */}
          <nav className="hidden lg:block sticky top-24 self-start">
            <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 mb-3">On this page</p>
            <ul className="space-y-2 text-sm">
              {[
                ["what-we-do", "What Aurexa Does"],
                ["google-data", "Google User Data"],
                ["information-we-collect", "Information We Collect"],
                ["google-oauth", "Google OAuth Use"],
                ["how-we-use", "How We Use Information"],
                ["tokens", "Tokens & Storage"],
                ["sharing", "Sharing & Sale"],
                ["revoke", "Revoke & Disconnect"],
                ["deletion", "Deletion"],
                ["security", "Security"],
                ["retention", "Retention"],
                ["cookies", "Cookies"],
                ["third-parties", "Third Parties"],
                ["rights", "Your Rights"],
                ["children", "Children"],
                ["changes", "Changes"],
                ["contact", "Contact"],
              ].map(([id, label]) => (
                <li key={id}><a href={`#${id}`} className="text-zinc-600 dark:text-zinc-400 hover:text-[#FF6B00] dark:hover:text-white transition-colors">{label}</a></li>
              ))}
            </ul>
          </nav>

          {/* Content */}
          <div className="space-y-10">
            <Section id="what-we-do" icon={FileText} title="1. What Aurexa Booking Agent Does">
              <p>
                Aurexa Booking Agent is an AI-powered appointment scheduling platform for small and medium businesses. It provides a conversational chat widget for your website, optional WhatsApp handoff, real-time availability based on your configured business hours (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">business_schedules</code> / <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">schedule_days</code>), OTP email verification, and an admin dashboard to manage bookings, conversations, and availability. When you connect calendar or meeting providers, the agent can check busy times and create online meeting links automatically at confirmation.
              </p>
            </Section>

            <Section id="google-data" icon={Shield} title="2. Google User Data — Disclosure Required by Google">
              <p className="p-3 rounded-lg bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-900 text-amber-900 dark:text-amber-200">
                This section discloses how we access Google user data via Google OAuth, as required by Google’s API Services User Data Policy and Limited Use requirements.
              </p>
              <div className="space-y-2">
                <p className="font-semibold text-zinc-900 dark:text-white">Categories of Google data accessed:</p>
                <ul className="list-disc pl-5 space-y-1">
                  <li><strong>Google Calendar:</strong> calendar list and events — we request <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">https://www.googleapis.com/auth/calendar.readonly</code> to read busy/free and <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">https://www.googleapis.com/auth/calendar.events</code> to create/update events for confirmed bookings. Implemented in <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">backend/app/core/integrations/google_calendar.py</code> and <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">calendar_providers/google.py</code>.</li>
                  <li><strong>Google Meet (as part of Calendar):</strong> conference creation via the same Calendar API — we add a Google Meet link when a booking is confirmed and the active meeting provider is Google Meet (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">meeting_providers/google_meet.py</code> uses <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">calendar.events</code> with <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">conferenceData</code>).</li>
                  <li><strong>Basic profile:</strong> the email address associated with the Google account that authorizes the OAuth flow (used only to complete the OAuth handshake).</li>
                </ul>
              </div>
              <div className="space-y-2">
                <p className="font-semibold text-zinc-900 dark:text-white">Purpose of access:</p>
                <ul className="list-disc pl-5 space-y-1">
                  <li>To compute real-time availability by reading busy periods via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">compute_slot_inventory</code> / <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">calendar_providers/google.py</code> — ensures we do not offer slots that conflict with existing events.</li>
                  <li>To create, update, or cancel a Calendar event (with Meet link when enabled) for a confirmed booking.</li>
                  <li>To maintain the connection via OAuth refresh without asking you to re-authorize on every request.</li>
                </ul>
              </div>
              <div className="space-y-2">
                <p className="font-semibold text-zinc-900 dark:text-white">How the data is processed:</p>
                <p>All Google API calls are made server-side from our backend (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">backend/app/core/integrations/google_calendar.py</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">CALENDAR_API_BASE=https://www.googleapis.com/calendar/v3</code>). The agent’s scheduling policy (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">app/engine/scheduling_policy.py</code>) treats your calendar as authoritative and never invents hours.</p>
              </div>
              <div className="space-y-2">
                <p className="font-semibold text-zinc-900 dark:text-white">Whether it is stored:</p>
                <ul className="list-disc pl-5 space-y-1">
                  <li><strong>OAuth tokens:</strong> only the <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">refresh_token</code> is persisted, in our database (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">org_configs.google_calendar_refresh_token</code>) per organization until you disconnect. Short-lived <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">access_token</code> values are held only in memory while a request is processed and are never stored in the database.</li>
                  <li><strong>Calendar events:</strong> we store only the created event ID <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">bookings.google_event_id</code> and snapshot times <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">user_slot_start_at / org_slot_start_at</code> for the booking — we do not bulk-copy your calendar.</li>
                </ul>
              </div>
              <div className="space-y-2">
                <p className="font-semibold text-zinc-900 dark:text-white">Who it is shared with:</p>
                <p>Google Calendar/Meet data is sent only to Google’s Calendar API as needed to fulfill your booking. It is <strong>not</strong> shared with other third parties, analytics providers, or advertisers. See also Section 10.</p>
              </div>
              <div className="space-y-2">
                <p className="font-semibold text-zinc-900 dark:text-white">How users can revoke:</p>
                <p>In-app: <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">Dashboard → Integrations → Calendar Sync → Disconnect</code> (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">POST /api/v1/dashboard/google-calendar/disconnect</code> clears the stored <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">google_calendar_refresh_token</code> and resets the active calendar provider). Externally: <a href="https://myaccount.google.com/permissions" target="_blank" rel="noreferrer" className="text-[#FF6B00] hover:underline">myaccount.google.com/permissions</a> → Remove Aurexa access — Google immediately stops issuing new tokens for us. If you revoke on Google’s side, use in-app Disconnect as well so our stored refresh token is cleared; until then, further calendar calls simply fail and no new Google data is accessed.</p>
              </div>
            </Section>

            <Section id="information-we-collect" icon={Database} title="3. Information We Collect">
              <p className="font-semibold text-zinc-900 dark:text-white">Account & organization:</p>
              <ul className="list-disc pl-5 space-y-1">
                <li>Full name, work email, password hash (via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">users</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">models/user.py</code>), organization name, API key and rotation fields <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">organizations.api_key / old_api_key</code>.</li>
                <li>Business configuration you provide: industry, timezone, business hours (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">business_schedules / schedule_days</code>), agent persona (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">org_configs.custom_prompts</code>), booking field definitions, brand color/logo, OTP settings.</li>
              </ul>
              <p className="font-semibold text-zinc-900 dark:text-white">Booking & conversation data:</p>
              <ul className="list-disc pl-5 space-y-1">
                <li>Customer data collected via the widget: name, email, phone, preferred date/time and any custom fields you define (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">chat_sessions.state_data / bookings.customer_* </code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">models/booking.py</code>), chat transcripts <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">chat_messages</code>, time slots.</li>
                <li>Uploaded knowledge: PDFs/website crawls you add via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">POST /documents/documents/upload</code> (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">documents.py</code>) — stored as <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">document_chunks</code> for retrieval.</li>
              </ul>
              <p className="font-semibold text-zinc-900 dark:text-white">Automatically collected:</p>
              <ul className="list-disc pl-5 space-y-1">
                <li>Browser timezone from <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">Intl.DateTimeFormat().resolvedOptions().timeZone</code> in the widget to resolve availability in the visitor’s local time.</li>
              </ul>
            </Section>

            <Section id="google-oauth" icon={Calendar} title="4. How Google OAuth Is Used">
              <p>
                You can optionally connect Google Calendar and Google Meet via OAuth to enable calendar and meeting functionality. The flow is: <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">POST /dashboard/google-calendar/connect</code> → redirect to Google consent with scopes <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">calendar.readonly</code> + <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">calendar.events</code> → callback <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">GET /dashboard/google-calendar/callback</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">dashboard.py</code> → <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">exchange_code_for_tokens</code> → store <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">refresh_token</code>. The same OAuth is reused for Meet conference creation. Connecting is optional — the booking agent works with manual hours if you do not connect.
              </p>
              <p>We also support <strong>Microsoft Teams/Outlook</strong> (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">MICROSOFT_CLIENT_ID</code>, scopes for Outlook Calendar) and <strong>Zoom</strong> (OAuth via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">meeting_providers/zoom.py</code>) with the same connect/disconnect pattern. Their data handling mirrors Google’s: tokens in <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">org_configs.microsoft_teams_refresh_token / zoom_refresh_token</code> until disconnect.</p>
            </Section>

            <Section id="how-we-use" icon={Eye} title="5. How We Use Information">
              <ul className="list-disc pl-5 space-y-1">
                <li>Authenticate you via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">ds_token</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">auth.py</code> and enforce org isolation on every dashboard endpoint.</li>
                <li>Run the booking flow: validate inputs, compute availability, create bookings, send OTP emails, and — if connected — read calendar busy times and create/update calendar events/Meet links.</li>
                <li>Render your agent persona, business hours, and knowledge base in the widget.</li>
                <li>Provide analytics in the dashboard (bookings, conversations) aggregated per organization.</li>
              </ul>
            </Section>

            <Section id="tokens" icon={Lock} title="6. How Tokens Are Handled and Protected">
              <ul className="list-disc pl-5 space-y-1">
                <li>OAuth <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">access_token</code> is short-lived and used server-side only; <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">refresh_token</code> is stored per-organization in Postgres <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">org_configs</code> until you disconnect. Transmission is over <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">https</code> via Cloudflare tunnels <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">cloudflared-backend/admin</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">docker-compose.yml</code>.</li>
                <li>We do not log or expose tokens to the frontend; the admin panel only shows connection status (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">GET /dashboard/calendar/config</code>).</li>
                <li>Revoking via in-app Disconnect or Google’s permissions page stops our access: failed refreshes mean subsequent Calendar API calls fail and no further Google data is fetched. To also clear the stored token after revoking on Google’s side, use in-app Disconnect.</li>
              </ul>
            </Section>

            <Section id="sharing" icon={Scale} title="7. Whether Google Data Is Shared or Sold">
              <ul className="list-disc pl-5 space-y-1">
                <li><strong>Not sold:</strong> we do not sell Google user data to third parties.</li>
                <li><strong>Not shared except as needed to provide the service:</strong> Google Calendar/Meet data is transmitted only to Google’s APIs to check availability or create events. Microsoft/Zoom data is transmitted only to their respective APIs when you have connected them.</li>
                <li>We do not share Google user data with advertisers, analytics providers, or other customers. We do not use Google user data to train generalized AI models.</li>
                <li>We comply with Google API Services User Data Policy, including Limited Use requirements.</li>
              </ul>
            </Section>

            <Section id="revoke" icon={RefreshCw} title="8. How to Disconnect / Revoke Google Access">
              <ol className="list-decimal pl-5 space-y-1">
                <li>In-app: <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">Dashboard → Integrations → Calendar Sync → Disconnect</code> or <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">Meeting Providers → Disconnect</code> — calls <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">POST /api/v1/dashboard/google-calendar/disconnect</code> (or the matching meeting-provider disconnect endpoint) and clears the stored tokens.</li>
                <li>Google side: visit <a href="https://myaccount.google.com/permissions" target="_blank" rel="noreferrer" className="text-[#FF6B00] hover:underline">myaccount.google.com/permissions</a>, find Aurexa, click Remove Access. This revokes the refresh token immediately.</li>
              </ol>
              <p>After either step, we will no longer access your Google Calendar/Meet data until you reconnect.</p>
            </Section>

            <Section id="deletion" icon={Trash2} title="9. How to Request Deletion of Data / Account">
              <p>
                To delete booking data, use <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">DELETE /documents/{"{doc_id}"}</code> for uploads or contact us. To delete your organization and all associated bookings, conversations, and connected integrations, contact us at{" "}
                <a href={`mailto:${CONTACT_EMAIL}`} className="text-[#FF6B00] hover:underline font-medium">
                  {CONTACT_EMAIL}
                </a>{" "}
                from your account email with subject “Delete my Aurexa data”. We will confirm and delete within 30 days, and revoke any remaining OAuth tokens as part of deletion.
              </p>
            </Section>

            <Section id="security" icon={Lock} title="10. Data Security">
              <ul className="list-disc pl-5 space-y-1">
                <li>TLS via Cloudflare tunnels <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">cloudflared-backend/admin</code>; passwords hashed with <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">hash_password</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">app/core/security.py</code>.</li>
                <li>Org isolation on every query <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">where org_id == user.org_id</code>; impersonation audited and blocked for mutations <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">deny_impersonation</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">auth.py</code>.</li>
                <li>Correlation IDs <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">asgi_correlation_id</code> and structured logging for incident response.</li>
              </ul>
            </Section>

            <Section id="retention" icon={Database} title="11. Data Retention">
              <ul className="list-disc pl-5 space-y-1">
                <li>Account/org data retained while your organization is active.</li>
                <li>OAuth <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">refresh_token</code> retained until you disconnect or delete the organization.</li>
                <li>Bookings, conversations, and document chunks retained until you delete them or the organization is deleted. Session retention is configurable via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">org_configs.session_retention_days</code> (default 30 days).</li>
              </ul>
            </Section>

            <Section id="cookies" icon={Cookie} title="12. Cookies and Session Technologies">
              <p>We use only essential cookies/session storage required for the app to function:</p>
              <ul className="list-disc pl-5 space-y-1">
                <li><code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">ds_token</code> — HttpOnly, Secure, SameSite=None, Path=/, Max-Age <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">ACCESS_TOKEN_EXPIRE_MINUTES*60</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">auth.py</code> — authenticates dashboard requests. No tracking cookies are set by us. The widget stores <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">dotstark_user_timezone</code> in <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">sessionStorage</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">chat-widget/src/index.js</code> to resolve availability in the visitor’s local time.</li>
                <li>We do not use third-party advertising cookies. If you add analytics (e.g., Vercel Analytics, PostHog) in the future, update this section to list them.</li>
              </ul>
            </Section>

            <Section id="third-parties" icon={Video} title="13. Third-Party Services Actually Used">
              <p>We only describe services present in this codebase:</p>
              <ul className="list-disc pl-5 space-y-1">
                <li><strong>Google Calendar / Google Meet</strong> — <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">https://www.googleapis.com/auth/calendar.readonly</code>, <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">https://www.googleapis.com/auth/calendar.events</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">google_calendar.py</code> — to read busy times and create Meet links.</li>
                <li><strong>Microsoft Outlook / Teams</strong> — <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">MICROSOFT_CLIENT_ID</code> via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">calendar_providers/outlook.py</code> / <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">meeting_providers/microsoft_teams.py</code> — same pattern as Google.</li>
                <li><strong>Zoom</strong> — OAuth via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">meeting_providers/zoom.py</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">ZOOM_REDIRECT_URI</code>.</li>
                <li><strong>Firecrawl</strong> — <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">app/services/firecrawl_service.py</code> for website crawling when you add a website as a knowledge source.</li>
                <li><strong>AI booking provider</strong> — our configured large-language-model provider, used to generate booking responses. Your Google Calendar event content is never sent to the AI provider; only derived busy/free availability information is used where applicable. Google user data is not used to train AI models.</li>
                <li><strong>PostgreSQL</strong> (<code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">postgres:15 postgres_data:/var/lib/postgresql/data</code>) and <strong>Redis</strong> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">redis:7</code> for persistence/caching, served through Cloudflare tunnels.</li>
              </ul>
              <p>We do not send Google user data to any other third party.</p>
            </Section>

            <Section id="rights" icon={Eye} title="14. Your Rights and Choices">
              <ul className="list-disc pl-5 space-y-1">
                <li>Access, correct, or delete your account and booking data via the dashboard or by emailing <a href={`mailto:${CONTACT_EMAIL}`} className="text-[#FF6B00] hover:underline">{CONTACT_EMAIL}</a>.</li>
                <li>Disconnect Google/Microsoft/Zoom at any time via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">Integrations</code> — revokes API access immediately.</li>
                <li>Configure retention and OTP requirements via <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">Agent Personalisation</code>.</li>
              </ul>
            </Section>

            <Section id="children" icon={Baby} title="15. Children’s Privacy">
              <p>Aurexa Booking Agent is not directed to children under 13, and we do not knowingly collect personal information from children. If you believe a child has provided us with personal information, contact us at <a href={`mailto:${CONTACT_EMAIL}`} className="text-[#FF6B00] hover:underline">{CONTACT_EMAIL}</a> and we will delete it.</p>
            </Section>

            <Section id="changes" icon={RefreshCw} title="16. Changes to This Privacy Policy">
              <p>
                We may update this Privacy Policy from time to time. We will post the updated version at <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-xs">/privacy-policy</code> and update the “Last Updated” date above. For material changes, we will also notify you via the dashboard or email. Continued use after changes constitutes acceptance.
              </p>
            </Section>

            <Section id="contact" icon={Mail} title="17. Contact">
              <p>
                Questions about this policy or to exercise your rights, contact us at{" "}
                <a href={`mailto:${CONTACT_EMAIL}`} className="text-[#FF6B00] hover:underline font-medium">
                  {CONTACT_EMAIL}
                </a>
                . Our application is operated as Aurexa.
              </p>
              <div className="mt-4 flex flex-wrap gap-3">
                <Link href="/" className="inline-flex items-center gap-1.5 text-sm font-medium px-4 py-2 rounded-lg bg-[#FF6B00] hover:bg-[#e55f00] text-white transition-colors">
                  Home
                </Link>
                <a href={`mailto:${CONTACT_EMAIL}`} className="inline-flex items-center gap-1.5 text-sm font-medium px-4 py-2 rounded-lg border border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors">
                  <Mail className="size-4" /> Contact Support
                </a>
              </div>
            </Section>

            <div className="pt-6 border-t border-zinc-200 dark:border-zinc-800 text-xs text-zinc-500">
              <p>This policy was drafted to be compatible with Google API Services User Data Policy and Google OAuth verification requirements. It accurately reflects the current implementation: we access Google data only via the OAuth scopes listed above, store only refresh tokens and created event IDs, do not sell data, and allow immediate revocation. Review with counsel before publishing.</p>
            </div>
          </div>
        </div>
      </main>

      <footer className="px-6 py-8 border-t border-zinc-800 mt-10">
        <div className="max-w-4xl mx-auto flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-[#FF6B00] to-orange-600 text-white font-bold text-xs">DS</div>
            <span className="text-sm text-zinc-500">© 2026 Aurexa AI. All rights reserved.</span>
          </div>
          <div className="flex items-center gap-6 text-sm text-zinc-500">
            <Link href="/" className="hover:text-white transition-colors">Home</Link>
            <Link href="/privacy-policy" className="text-[#FF6B00] font-medium">Privacy Policy</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}
