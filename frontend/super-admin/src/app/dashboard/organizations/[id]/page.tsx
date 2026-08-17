"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { ConfirmDialog, Modal } from "@/components/modal";
import { AuditAction, AuditChanges } from "@/components/audit/audit-change";

type OrgConfig = {
  timezone: string | null;
  require_otp: boolean;
  allow_same_day_booking: boolean;
  slot_cutoff_minutes: number;
  max_booking_days_ahead: number;
  session_retention_days: number;
  meeting_mode: string | null;
  active_calendar_provider: string | null;
  active_meeting_provider: string | null;
  whatsapp_enabled: boolean;
  google_calendar_enabled: boolean;
  outlook_calendar_enabled: boolean;
  website_url: string | null;
  business_name: string | null;
};

type OrgDetail = {
  id: string;
  name: string;
  business_name: string | null;
  is_active: boolean;
  api_key_masked: string;
  created_at: string | null;
  user_count: number;
  booking_count: number;
  conversation_count: number;
  session_retention_days: number | null;
  last_activity_at: string | null;
  config: OrgConfig;
};

type OrgSubRow = {
  id: string;
  org_id: string;
  org_name: string;
  plan_id: string;
  plan_code: string;
  plan_name: string;
  provider: string;
  status: string;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  canceled_at: string | null;
  created_at: string | null;
};

type OrgInvoiceRow = {
  id: string;
  org_id: string;
  org_name: string;
  number: string;
  amount_cents: number;
  currency: string;
  status: string;
  invoice_url: string | null;
  description: string | null;
  due_at: string | null;
  paid_at: string | null;
  refunded_at: string | null;
  refund_amount_cents: number;
  created_at: string | null;
};

type Booking = {
  id: string;
  customer_name: string;
  customer_email: string;
  customer_phone: string | null;
  org_slot_start_at: string | null;
  org_slot_end_at: string | null;
  meeting_link_url: string | null;
  meeting_type_snapshot: string | null;
  created_at: string | null;
};

type OrgUser = {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string | null;
};

type UsageRow = { metric: string; count: number; last_updated: string | null };

type AuditRow = {
  id: string;
  actor_email: string | null;
  action: string;
  old_values: unknown;
  new_values: unknown;
  created_at: string | null;
};

type Conversation = {
  id: string;
  channel: string;
  channel_user_id: string | null;
  session_status: string;
  current_intent: string | null;
  human_agent_active: boolean;
  message_count: number;
  updated_at: string;
};

type PeekMessage = {
  id: string;
  role: string;
  content: string;
  sender_type: string;
  created_at: string;
};

type PeekResult = {
  session_id: string;
  channel: string;
  channel_user_id: string | null;
  session_status: string;
  state_data: Record<string, unknown>;
  messages: PeekMessage[];
};

type ImpActive = {
  id: string;
  org_id: string;
  org_name: string;
  user_email: string;
  started_at: string;
  expires_at: string;
  status: string;
};

const TABS = [
  "Overview",
  "Users",
  "Usage",
  "Billing",
  "Integrations",
  "Audit",
  "Support",
] as const;

type Tab = (typeof TABS)[number];

function Badge({ on }: { on: boolean }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
        on ? "bg-green-900/60 text-green-300" : "bg-slate-800 text-slate-400"
      }`}
    >
      {on ? "ON" : "OFF"}
    </span>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-8 text-center text-sm text-slate-500">
      {text}
    </div>
  );
}

export default function OrgDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { can } = useAuth();
  const { toast } = useToast();
  const [org, setOrg] = useState<OrgDetail | null>(null);
  const [tab, setTab] = useState<Tab>("Overview");
  const [error, setError] = useState<string | null>(null);

  const [bookings, setBookings] = useState<Booking[]>([]);
  const [users, setUsers] = useState<OrgUser[]>([]);
  const [usage, setUsage] = useState<UsageRow[]>([]);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [billingSubs, setBillingSubs] = useState<OrgSubRow[]>([]);
  const [billingInvoices, setBillingInvoices] = useState<OrgInvoiceRow[]>([]);
  const [tabError, setTabError] = useState<string | null>(null);
  const canUnmask = can("pii.unmask");
  const canImpersonate = can("support.impersonate");

  const [confirm, setConfirm] = useState<
    | { kind: "suspend" }
    | { kind: "restore" }
    | { kind: "delete" }
    | { kind: "disable"; user: OrgUser }
    | { kind: "enable"; user: OrgUser }
    | null
  >(null);
  const [busy, setBusy] = useState(false);
  const [deleteModal, setDeleteModal] = useState(false);
  const [deleteResult, setDeleteResult] = useState<string | null>(null);

  const loadOrg = useCallback(async () => {
    setError(null);
    try {
      setOrg(await api.get<OrgDetail>(`/tenants/orgs/${id}`));
    } catch (e) {
      if (e instanceof Error && e.message === "Unauthorized") {
        router.replace("/login");
        return;
      }
      setError(e instanceof Error ? e.message : "Failed to load organization");
    }
  }, [id, router]);

  useEffect(() => {
    loadOrg();
  }, [loadOrg]);

  const loadTab = useCallback(
    async (t: Tab) => {
      setTabError(null);
      try {
        if (t === "Overview") {
          const b = await api.get<{ items: Booking[] }>(
            `/tenants/orgs/${id}/bookings?page_size=10`
          );
          setBookings(b.items);
        } else if (t === "Users") {
          const u = await api.get<{ items: OrgUser[] }>(
            `/tenants/orgs/${id}/users`
          );
          setUsers(u.items);
        } else if (t === "Usage") {
          const u = await api.get<{ items: UsageRow[] }>(
            `/tenants/orgs/${id}/usage`
          );
          setUsage(u.items);
        } else if (t === "Billing") {
          const [s, inv] = await Promise.all([
            api.get<{ items: OrgSubRow[] }>(
              `/billing/subscriptions?org_id=${id}`
            ),
            api.get<{ items: OrgInvoiceRow[] }>(
              `/billing/invoices?org_id=${id}`
            ),
          ]);
          setBillingSubs(s.items);
          setBillingInvoices(inv.items);
        } else if (t === "Audit") {
          const a = await api.get<{ items: AuditRow[] }>(
            `/tenants/orgs/${id}/audit`
          );
          setAudit(a.items);
        } else if (t === "Support") {
          setConvLoading(true);
          setConversations(null);
          try {
            const c = await api.get<{ items: Conversation[] }>(
              `/support/orgs/${id}/conversations`
            );
            setConversations(c.items);
            if (canImpersonate) {
              const a = await api.get<ImpActive | null>(`/impersonation/active`);
              setActiveImp(a);
              const u = await api.get<{ items: OrgUser[] }>(
                `/tenants/orgs/${id}/users?page_size=100&unmask=true`
              );
              setImpUsers(u.items.filter((x) => x.is_active));
            }
          } finally {
            setConvLoading(false);
          }
        }
      } catch (e) {
        setTabError(e instanceof Error ? e.message : "Failed to load tab");
      }
    },
    [id, canImpersonate]
  );

  const switchTab = (t: Tab) => {
    setTab(t);
    loadTab(t);
  };

  useEffect(() => {
    loadTab("Overview");
  }, [loadTab]);

  const runAction = async (
    action: string,
    request: () => Promise<unknown>,
    successMsg: string
  ) => {
    setBusy(true);
    try {
      await request();
      toast("success", successMsg);
      setConfirm(null);
      await loadOrg();
      if (tab === "Users") await loadTab("Users");
      if (action === "org.delete" && tab === "Audit") await loadTab("Audit");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const confirmRun = () => {
    if (!confirm || !org) return;
    if (confirm.kind === "suspend")
      void runAction(
        "org.suspend",
        () => api.post(`/lifecycle/orgs/${org.id}/suspend`),
        `${org.name} suspended`
      );
    else if (confirm.kind === "restore")
      void runAction(
        "org.restore",
        () => api.post(`/lifecycle/orgs/${org.id}/restore`),
        `${org.name} restored`
      );
    else if (confirm.kind === "disable" || confirm.kind === "enable") {
      const enable = confirm.kind === "enable";
      void runAction(
        enable ? "user.enable" : "user.disable",
        () =>
          api.post(`/lifecycle/users/${confirm.user.id}/${enable ? "enable" : "disable"}`),
        `${confirm.user.email} ${enable ? "enabled" : "disabled"}`
      );
    }
  };

  const requestDelete = async () => {
    setBusy(true);
    try {
      const res = await api.post<{ approval_id: string }>("/approvals", {
        request_type: "org.delete",
        resource_id: org?.id,
        payload: { name: org?.name },
      });
      setDeleteResult(
        `Delete request filed (${res.approval_id}). A second admin must approve it for the deletion to execute.`
      );
      toast("info", "Delete request filed — pending second-admin approval");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to file delete request");
    } finally {
      setBusy(false);
    }
  };

  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [convLoading, setConvLoading] = useState(false);
  const [peekOpen, setPeekOpen] = useState(false);
  const [peeking, setPeeking] = useState(false);
  const [peekResult, setPeekResult] = useState<PeekResult | null>(null);
  const [revealed, setRevealed] = useState(false);
  const [revealOpen, setRevealOpen] = useState(false);
  const [revealCode, setRevealCode] = useState("");
  const [revealReason, setRevealReason] = useState("");
  const [revealBusy, setRevealBusy] = useState(false);
  const [peekReqOpen, setPeekReqOpen] = useState(false);
  const [peekReqBusy, setPeekReqBusy] = useState(false);
  const [peekReqId, setPeekReqId] = useState<string | null>(null);
  const [peekReqSentTo, setPeekReqSentTo] = useState("");
  const [peekReqStatus, setPeekReqStatus] = useState<string | null>(null);
  const [pendingPeekId, setPendingPeekId] = useState<string | null>(null);
  const [unmaskReqOpen, setUnmaskReqOpen] = useState(false);
  const [unmaskReqBusy, setUnmaskReqBusy] = useState(false);
  const [unmaskReqId, setUnmaskReqId] = useState<string | null>(null);
  const [unmaskReqSentTo, setUnmaskReqSentTo] = useState("");
  const [unmaskReqStatus, setUnmaskReqStatus] = useState<string | null>(null);
  const [impUsers, setImpUsers] = useState<OrgUser[] | null>(null);
  const [impUser, setImpUser] = useState<OrgUser | null>(null);
  const [stepupOpen, setStepupOpen] = useState(false);
  const [stepupCode, setStepupCode] = useState("");
  const [stepupBusy, setStepupBusy] = useState(false);
  const [issuedToken, setIssuedToken] = useState<string | null>(null);
  const [activeImp, setActiveImp] = useState<ImpActive | null>(null);
  const [stoppingImp, setStoppingImp] = useState(false);

  const openPeek = async (sessionId: string) => {
    setPendingPeekId(sessionId);
    try {
      const g = await api.get<{ granted: boolean }>(
        `/support/peek/grant?org_id=${id}`
      );
      if (g.granted) {
        void doLoadTranscript(sessionId);
      } else {
        setPeekReqStatus(null);
        setPeekReqId(null);
        setPeekReqSentTo("");
        setPeekReqOpen(true);
      }
    } catch {
      setPeekReqStatus(null);
      setPeekReqId(null);
      setPeekReqSentTo("");
      setPeekReqOpen(true);
    }
  };

  const doLoadTranscript = async (sessionId: string) => {
    setPeekOpen(true);
    setPeeking(true);
    setPeekResult(null);
    setRevealed(false);
    try {
      setPeekResult(
        await api.get<PeekResult>(`/support/sessions/${sessionId}/peek`)
      );
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to load transcript");
      setPeekOpen(false);
    } finally {
      setPeeking(false);
    }
  };

  const doRequestPeekConsent = async () => {
    setPeekReqBusy(true);
    try {
      const r = await api.post<{
        id: string;
        link_sent_to: string;
        link_expires_at: string;
        link_ttl_hours: number;
      }>("/support/peek/request", { org_id: id });
      setPeekReqId(r.id);
      setPeekReqSentTo(r.link_sent_to);
      setPeekReqStatus("pending");
      toast("info", `Consent link sent to ${r.link_sent_to}`);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to send consent request");
    } finally {
      setPeekReqBusy(false);
    }
  };

  const doCancelPeekRequest = () => {
    setPeekReqOpen(false);
    setPeekReqId(null);
    setPeekReqSentTo("");
    setPeekReqStatus(null);
  };

  useEffect(() => {
    if (!peekReqOpen || !peekReqId) return;
    let cancelled = false;
    const check = async () => {
      try {
        const r = await api.get<{ status: string }>(
          `/support/peek/request/${peekReqId}`
        );
        if (cancelled) return;
        setPeekReqStatus(r.status);
        if (r.status === "approved") {
          setPeekReqOpen(false);
          setPeekReqId(null);
          toast("success", "Org admin approved — transcript unlocked");
          if (pendingPeekId) void doLoadTranscript(pendingPeekId);
        }
      } catch {
        // transient — keep polling
      }
    };
    void check();
    const t = setInterval(check, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [peekReqOpen, peekReqId, pendingPeekId]);

  const revealPeek = async () => {
    if (!peekResult) return;
    setRevealBusy(true);
    try {
      await api.post("/auth/mfa/stepup", { code: revealCode });
      const r = await api.get<PeekResult>(
        `/support/sessions/${peekResult.session_id}/peek?unmask=true&reason=${encodeURIComponent(revealReason.trim())}`
      );
      setPeekResult(r);
      setRevealed(true);
      setRevealOpen(false);
      setRevealCode("");
      setRevealReason("");
      toast("success", "Transcript revealed and audit-logged");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to reveal transcript");
    } finally {
      setRevealBusy(false);
    }
  };

  const openReveal = async () => {
    try {
      const g = await api.get<{ unmask_granted: boolean }>(
        `/support/peek/grant?org_id=${id}`
      );
      if (!g.unmask_granted) {
        setUnmaskReqStatus(null);
        setUnmaskReqId(null);
        setUnmaskReqSentTo("");
        setUnmaskReqOpen(true);
        return;
      }
    } catch {
      // grant check failed — fall through to the reveal modal; the server
      // still enforces org-admin approval.
    }
    setRevealOpen(true);
  };

  const doRequestUnmaskConsent = async () => {
    setUnmaskReqBusy(true);
    try {
      const r = await api.post<{
        id: string;
        link_sent_to: string;
      }>("/support/peek/request", { org_id: id, purpose: "unmask" });
      setUnmaskReqId(r.id);
      setUnmaskReqSentTo(r.link_sent_to);
      setUnmaskReqStatus("pending");
      toast("info", `Approval link sent to ${r.link_sent_to}`);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to send approval request");
    } finally {
      setUnmaskReqBusy(false);
    }
  };

  useEffect(() => {
    if (!unmaskReqOpen || !unmaskReqId) return;
    let cancelled = false;
    const check = async () => {
      try {
        const r = await api.get<{ status: string }>(
          `/support/peek/request/${unmaskReqId}`
        );
        if (cancelled) return;
        setUnmaskReqStatus(r.status);
        if (r.status === "approved") {
          setUnmaskReqOpen(false);
          setUnmaskReqId(null);
          toast("success", "Org admin approved — PII reveal unlocked");
          setRevealOpen(true);
        }
      } catch {
        // transient — keep polling
      }
    };
    void check();
    const t = setInterval(check, 5000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [unmaskReqOpen, unmaskReqId]);

  const startImpersonation = async () => {
    if (!impUser) return;
    setStepupBusy(true);
    try {
      await api.post("/auth/mfa/stepup", { code: stepupCode });
      const res = await api.post<{ token: string }>("/impersonation/start", {
        org_id: org?.id,
        user_email: impUser.email,
      });
      setIssuedToken(res.token);
      toast("success", `Impersonation started as ${impUser.email}`);
      setStepupOpen(false);
      setStepupCode("");
      await loadTab("Support");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to start impersonation");
    } finally {
      setStepupBusy(false);
    }
  };

  const stopImpersonation = async () => {
    if (!activeImp) return;
    setStoppingImp(true);
    try {
      await api.post(`/impersonation/${activeImp.id}/stop`);
      toast("success", "Impersonation ended");
      await loadTab("Support");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to end impersonation");
    } finally {
      setStoppingImp(false);
    }
  };

  if (error) {
    return (
      <div>
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
        <Link
          href="/dashboard/organizations"
          className="text-sm text-indigo-400 hover:underline"
        >
          ← Back to organizations
        </Link>
      </div>
    );
  }

  if (!org) return <p className="text-slate-400">Loading…</p>;

  return (
    <div>
      <Link
        href="/dashboard/organizations"
        className="text-sm text-indigo-400 hover:underline"
      >
        ← Back to organizations
      </Link>

      <div className="mt-2 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{org.name}</h1>
          {org.business_name && (
            <p className="text-sm text-slate-400">{org.business_name}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              org.is_active
                ? "bg-green-900/60 text-green-300"
                : "bg-red-900/60 text-red-300"
            }`}
          >
            {org.is_active ? "ACTIVE" : "SUSPENDED"}
          </span>
          {org.is_active && can("orgs.suspend") && (
            <button
              onClick={() => setConfirm({ kind: "suspend" })}
              className="rounded-md border border-amber-700 px-3 py-1.5 text-sm text-amber-300 hover:bg-amber-950"
            >
              Suspend
            </button>
          )}
          {!org.is_active && can("orgs.restore") && (
            <button
              onClick={() => setConfirm({ kind: "restore" })}
              className="rounded-md border border-green-700 px-3 py-1.5 text-sm text-green-300 hover:bg-green-950"
            >
              Restore
            </button>
          )}
          {can("orgs.delete") && (
            <button
              onClick={() => {
                setDeleteResult(null);
                setDeleteModal(true);
              }}
              className="rounded-md border border-red-700 px-3 py-1.5 text-sm text-red-300 hover:bg-red-950"
            >
              Delete org
            </button>
          )}
        </div>
      </div>

      {confirm &&
        (confirm.kind === "suspend" || confirm.kind === "restore") && (
        <ConfirmDialog
          title={confirm.kind === "suspend" ? "Suspend organization" : "Restore organization"}
          message={
            confirm.kind === "suspend" ? (
              <>
                This blocks all access for <b>{org.name}</b>. Users will be unable to
                log in or book until the org is restored.
              </>
            ) : (
              <>
                Reactivate <b>{org.name}</b> and restore access for its users.
              </>
            )
          }
          confirmLabel={confirm.kind === "suspend" ? "Suspend" : "Restore"}
          danger={confirm.kind === "suspend"}
          busy={busy}
          onConfirm={confirmRun}
          onCancel={() => setConfirm(null)}
        />
      )}

      {confirm &&
        (confirm.kind === "disable" || confirm.kind === "enable") && (
        <ConfirmDialog
          title={confirm.kind === "disable" ? "Disable user" : "Enable user"}
          message={
            confirm.kind === "disable" ? (
              <>
                Block <b>{confirm.user.email}</b> from logging in. The user can be
                re-enabled at any time.
              </>
            ) : (
              <>
                Restore login access for <b>{confirm.user.email}</b>.
              </>
            )
          }
          confirmLabel={confirm.kind === "disable" ? "Disable" : "Enable"}
          danger={confirm.kind === "disable"}
          busy={busy}
          onConfirm={confirmRun}
          onCancel={() => setConfirm(null)}
        />
      )}

      {deleteModal && (
        <Modal
          title="Delete organization"
          onClose={() => {
            setDeleteModal(false);
            setDeleteResult(null);
          }}
        >
          {deleteResult ? (
            <p className="text-sm text-slate-300">{deleteResult}</p>
          ) : (
            <>
              <p className="text-sm text-slate-300">
                Deleting <b>{org.name}</b> permanently removes the organization and
                all its data (bookings, users, sessions).
              </p>
              <p className="mt-2 text-xs text-amber-300">
                Deletion requires approval from a second admin within 24 hours.
              </p>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  onClick={() => setDeleteModal(false)}
                  disabled={busy}
                  className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={requestDelete}
                  disabled={busy}
                  className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
                >
                  {busy ? "Filing…" : "File delete request"}
                </button>
              </div>
            </>
          )}
        </Modal>
      )}

      <div className="mt-4 flex gap-1 border-b border-slate-800">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => switchTab(t)}
            className={`rounded-t-md px-4 py-2 text-sm font-medium ${
              tab === t
                ? "border border-b-0 border-slate-800 bg-slate-900 text-slate-100"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      {tabError && (
        <p className="mt-4 text-sm text-red-400" role="alert">
          {tabError}
        </p>
      )}

      {tab === "Overview" && (
        <div className="mt-4">
          <div className="grid grid-cols-4 gap-4">
            {[
              ["Users", org.user_count],
              ["Bookings", org.booking_count],
              ["Conversations", org.conversation_count],
              [
                "Created",
                org.created_at
                  ? new Date(org.created_at).toLocaleDateString()
                  : "—",
              ],
            ].map(([label, value]) => (
              <div
                key={label as string}
                className="rounded-lg border border-slate-800 bg-slate-900/50 p-4"
              >
                <p className="text-xs uppercase tracking-wide text-slate-400">
                  {label}
                </p>
                <p className="mt-1 text-xl font-semibold">{value}</p>
              </div>
            ))}
          </div>

          <div className="mt-4 rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Configuration
            </h2>
            <dl className="mt-3 grid grid-cols-2 gap-x-8 gap-y-2 text-sm">
              <div className="flex justify-between">
                <dt className="text-slate-400">Timezone</dt>
                <dd className="text-slate-200">{org.config.timezone ?? "UTC"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Require OTP</dt>
                <dd>
                  <Badge on={org.config.require_otp} />
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Same-day booking</dt>
                <dd>
                  <Badge on={org.config.allow_same_day_booking} />
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Slot cutoff (min)</dt>
                <dd className="text-slate-200">{org.config.slot_cutoff_minutes}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Max ahead (days)</dt>
                <dd className="text-slate-200">{org.config.max_booking_days_ahead}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Session retention</dt>
                <dd className="text-slate-200">
                  {org.config.session_retention_days} days
                </dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">Meeting mode</dt>
                <dd className="text-slate-200">{org.config.meeting_mode ?? "—"}</dd>
              </div>
              <div className="flex justify-between">
                <dt className="text-slate-400">API key</dt>
                <dd className="font-mono text-xs text-slate-400">
                  {org.api_key_masked}
                </dd>
              </div>
            </dl>
          </div>

          <div className="mt-4 rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Recent bookings
            </h2>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="px-2 py-2">Customer</th>
                    <th className="px-2 py-2">Email</th>
                    <th className="px-2 py-2">Slot</th>
                    <th className="px-2 py-2">Type</th>
                    <th className="px-2 py-2">Created</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800">
                  {bookings.map((b) => (
                    <tr key={b.id}>
                      <td className="px-2 py-2">{b.customer_name}</td>
                      <td className="px-2 py-2 text-slate-300">{b.customer_email}</td>
                      <td className="px-2 py-2 text-slate-300">
                        {b.org_slot_start_at
                          ? new Date(b.org_slot_start_at).toLocaleString()
                          : "—"}
                      </td>
                      <td className="px-2 py-2 text-slate-400">
                        {b.meeting_type_snapshot ?? "—"}
                      </td>
                      <td className="px-2 py-2 text-slate-400">
                        {b.created_at
                          ? new Date(b.created_at).toLocaleString()
                          : "—"}
                      </td>
                    </tr>
                  ))}
                  {bookings.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-2 py-6 text-center text-slate-500">
                        No bookings yet
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {tab === "Users" && (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-3">Name</th>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Joined</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-900/50">
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="px-4 py-3">
                    <Link
                      href={`/dashboard/users/${u.id}`}
                      className="font-medium text-indigo-400 hover:underline"
                    >
                      {u.full_name ?? "—"}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-300">{u.email}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        u.is_active
                          ? "bg-green-900/60 text-green-300"
                          : "bg-red-900/60 text-red-300"
                      }`}
                    >
                      {u.is_active ? "ACTIVE" : "DISABLED"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {u.created_at
                      ? new Date(u.created_at).toLocaleDateString()
                      : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    {u.is_active && can("users.disable") && (
                      <button
                        onClick={() => setConfirm({ kind: "disable", user: u })}
                        className="rounded border border-amber-700 px-2 py-1 text-xs text-amber-300 hover:bg-amber-950"
                      >
                        Disable
                      </button>
                    )}
                    {!u.is_active && can("users.enable") && (
                      <button
                        onClick={() => setConfirm({ kind: "enable", user: u })}
                        className="rounded border border-green-700 px-2 py-1 text-xs text-green-300 hover:bg-green-950"
                      >
                        Enable
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                    No users in this organization
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === "Usage" && (
        <div className="mt-4">
          {usage.length === 0 && (
            <EmptyState text="Usage counters appear here once telemetry is enabled (TELEMETRY_ENABLED=true)." />
          )}
          {usage.length > 0 && (
            <div className="mt-4 overflow-x-auto rounded-lg border border-slate-800">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
                  <tr>
                    <th className="px-4 py-3">Metric</th>
                    <th className="px-4 py-3">Count</th>
                    <th className="px-4 py-3">Last updated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800 bg-slate-900/50">
                  {usage.map((u) => (
                    <tr key={u.metric}>
                      <td className="px-4 py-3">
                        <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs">
                          {u.metric}
                        </span>{" "}
                        <span className="text-slate-400">
                          {u.metric === "bookings"
                            ? "bookings"
                            : u.metric === "conversations"
                              ? "chat conversations"
                              : "AI tokens (LLM)"}
                        </span>
                      </td>
                      <td className="px-4 py-3">{u.count}</td>
                      <td className="px-4 py-3 text-slate-400">
                        {u.last_updated
                          ? new Date(u.last_updated).toLocaleString()
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {tab === "Billing" && (
        <div className="mt-4 space-y-6">
          {tabError && (
            <div className="rounded-lg border border-red-800 bg-red-950/50 p-3 text-sm text-red-300">
              {tabError}
            </div>
          )}
          <div>
            <h3 className="mb-2 text-sm font-medium uppercase tracking-wide text-slate-400">
              Subscriptions
            </h3>
            {billingSubs.length === 0 ? (
              <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-6 text-center text-sm text-slate-500">
                No subscriptions for this organization
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                {billingSubs.map((s) => (
                  <div
                    key={s.id}
                    className="rounded-lg border border-slate-800 bg-slate-900/50 p-4"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-slate-200">
                        {s.plan_name}
                      </span>
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                          s.status === "active"
                            ? "bg-green-900/60 text-green-300"
                            : s.status === "canceled"
                              ? "bg-slate-800 text-slate-400"
                              : "bg-amber-900/60 text-amber-300"
                        }`}
                      >
                        {s.status.toUpperCase()}
                      </span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                      {s.plan_code} · {s.provider}
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-400">
                      <div>
                        <div className="text-slate-500">Period</div>
                        <div>
                          {s.current_period_start
                            ? `${new Date(s.current_period_start).toLocaleDateString()} → ${s.current_period_end ? new Date(s.current_period_end).toLocaleDateString() : "—"}`
                            : "—"}
                        </div>
                      </div>
                      <div>
                        <div className="text-slate-500">Created</div>
                        <div>
                          {s.created_at
                            ? new Date(s.created_at).toLocaleDateString()
                            : "—"}
                        </div>
                      </div>
                    </div>
                    {s.canceled_at && (
                      <div className="mt-2 text-xs text-red-400">
                        Canceled{" "}
                        {new Date(s.canceled_at).toLocaleDateString()}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div>
            <h3 className="mb-2 text-sm font-medium uppercase tracking-wide text-slate-400">
              Invoices
            </h3>
            {billingInvoices.length === 0 ? (
              <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-6 text-center text-sm text-slate-500">
                No invoices for this organization
              </div>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-slate-800">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
                    <tr>
                      <th className="px-4 py-3">Invoice</th>
                      <th className="px-4 py-3">Amount</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Issued</th>
                      <th className="px-4 py-3">Due</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 bg-slate-900/50">
                    {billingInvoices.map((inv) => (
                      <tr key={inv.id}>
                        <td className="px-4 py-3 font-mono text-slate-300">
                          {inv.number}
                        </td>
                        <td className="px-4 py-3 text-slate-200">
                          {(inv.amount_cents / 100).toLocaleString("en-US", {
                            style: "currency",
                            currency: inv.currency || "USD",
                          })}
                        </td>
                        <td className="px-4 py-3">
                          <span
                            className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                              inv.status === "paid"
                                ? "bg-green-900/60 text-green-300"
                                : inv.status === "refunded"
                                  ? "bg-indigo-900/60 text-indigo-300"
                                  : inv.status === "open"
                                    ? "bg-amber-900/60 text-amber-300"
                                    : "bg-slate-800 text-slate-400"
                            }`}
                          >
                            {inv.status.toUpperCase()}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs text-slate-400">
                          {inv.created_at
                            ? new Date(inv.created_at).toLocaleDateString()
                            : "—"}
                        </td>
                        <td className="px-4 py-3 text-xs text-slate-400">
                          {inv.due_at
                            ? new Date(inv.due_at).toLocaleDateString()
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {tab === "Integrations" && (
        <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {[
            ["WhatsApp", org.config.whatsapp_enabled],
            ["Google Calendar", org.config.google_calendar_enabled],
            ["Outlook Calendar", org.config.outlook_calendar_enabled],
          ].map(([label, on]) => (
            <div
              key={label as string}
              className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-4"
            >
              <span className="text-sm font-medium">{label}</span>
              <Badge on={on as boolean} />
            </div>
          ))}
          <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <span className="text-sm font-medium">Active calendar</span>
            <span className="text-sm text-slate-300">
              {org.config.active_calendar_provider ?? "NONE"}
            </span>
          </div>
          <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <span className="text-sm font-medium">Active meeting provider</span>
            <span className="text-sm text-slate-300">
              {org.config.active_meeting_provider ?? "NONE"}
            </span>
          </div>
          <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <span className="text-sm font-medium">Website (RAG)</span>
            <span className="max-w-[55%] truncate text-sm text-slate-300">
              {org.config.website_url ?? "—"}
            </span>
          </div>
        </div>
      )}

      {tab === "Audit" && (
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-3">Time</th>
                <th className="px-4 py-3">Actor</th>
                <th className="px-4 py-3">Action</th>
                <th className="px-4 py-3">Changes</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-900/50">
              {audit.map((a) => (
                <tr key={a.id}>
                  <td className="px-4 py-3 text-slate-300">
                    {a.created_at ? new Date(a.created_at).toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-3">{a.actor_email ?? "—"}</td>
                  <td className="px-4 py-3">
                    <AuditAction action={a.action} />
                  </td>
                  <td className="px-4 py-3">
                    <AuditChanges
                      action={a.action}
                      oldValues={a.old_values}
                      newValues={a.new_values}
                    />
                  </td>
                </tr>
              ))}
              {audit.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-8 text-center text-slate-400">
                    No control-plane actions for this organization yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === "Support" && (
        <div className="mt-4 space-y-6">
          <section>
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Live conversations
            </h2>
            {convLoading ? (
              <EmptyState text="Loading conversations…" />
            ) : conversations && conversations.length > 0 ? (
              <div className="mt-3 overflow-x-auto rounded-lg border border-slate-800">
                <table className="w-full text-left text-sm">
                  <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
                    <tr>
                      <th className="px-4 py-3">Channel</th>
                      <th className="px-4 py-3">User</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3">Intent</th>
                      <th className="px-4 py-3">Msgs</th>
                      <th className="px-4 py-3">Updated</th>
                      <th className="px-4 py-3"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 bg-slate-900/50">
                    {conversations.map((c) => (
                      <tr key={c.id}>
                        <td className="px-4 py-3">{c.channel}</td>
                        <td className="px-4 py-3 font-mono text-xs">
                          {c.channel_user_id ?? "—"}
                        </td>
                        <td className="px-4 py-3">
                          <span className="rounded bg-slate-800 px-2 py-0.5 text-xs">
                            {c.session_status}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs text-slate-300">
                          {c.current_intent ?? "—"}
                        </td>
                        <td className="px-4 py-3">{c.message_count}</td>
                        <td className="px-4 py-3 text-xs text-slate-400">
                          {new Date(c.updated_at).toLocaleString()}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <button
                            onClick={() => void openPeek(c.id)}
                            className="rounded-md border border-slate-700 px-3 py-1 text-xs text-slate-300 hover:bg-slate-800"
                          >
                            View transcript
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="mt-3">
                <EmptyState text="No conversations found for this organization." />
              </div>
            )}
          </section>

          <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Impersonate a user
            </h2>
            {canImpersonate ? (
              <div className="mt-3">
                {activeImp && (
                  <div className="mb-3 rounded-md border border-amber-800 bg-amber-950/40 p-3 text-sm">
                    <p className="font-semibold text-amber-300">
                      Active impersonation: {activeImp.user_email}
                    </p>
                    <p className="mt-1 text-xs text-amber-400/80">
                      Started {new Date(activeImp.started_at).toLocaleString()} · ends{" "}
                      {new Date(activeImp.expires_at).toLocaleString()}
                    </p>
                    <button
                      onClick={() => void stopImpersonation()}
                      disabled={stoppingImp}
                      className="mt-2 rounded-md border border-amber-700 bg-amber-900/60 px-3 py-1 text-xs font-medium text-amber-200 hover:bg-amber-900 disabled:opacity-50"
                    >
                      {stoppingImp ? "Ending…" : "End now"}
                    </button>
                  </div>
                )}
                <div className="flex flex-wrap items-end gap-3">
                  <label className="flex flex-col gap-1 text-xs text-slate-400">
                    User
                    <select
                      value={impUser?.id ?? ""}
                      onChange={(e) =>
                        setImpUser(
                          impUsers?.find((u) => u.id === e.target.value) ?? null
                        )
                      }
                      className="w-72 rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100"
                    >
                      <option value="">Select a user…</option>
                      {impUsers?.map((u) => (
                        <option key={u.id} value={u.id}>
                          {u.full_name ? `${u.full_name} · ` : ""}
                          {u.email}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    onClick={() => {
                      if (impUser) {
                        setStepupCode("");
                        setStepupOpen(true);
                      }
                    }}
                    disabled={!impUser || stepupBusy}
                    className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
                  >
                    Start impersonation
                  </button>
                </div>
                {impUsers?.length === 0 && (
                  <p className="mt-2 text-xs text-slate-500">
                    No active users in this organization to impersonate.
                  </p>
                )}
                {issuedToken && (
                  <div className="mt-3 rounded-md border border-green-800 bg-green-950/40 p-3">
                    <p className="text-xs font-semibold uppercase tracking-wide text-green-400">
                      Impersonation token issued
                    </p>
                    <p className="mt-1 break-all font-mono text-xs text-green-300">
                      {issuedToken}
                    </p>
                    <p className="mt-1 text-xs text-slate-400">
                      This token lets you act as the user (e.g. via{" "}
                      <span className="font-mono">/api/v1/me</span>). It expires
                      automatically; use the banner in your session and "End now"
                      above to revoke early.
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <p className="mt-3 text-sm text-slate-500">
                You need the <span className="font-mono">support.impersonate</span>{" "}
                permission to impersonate users.
              </p>
            )}
          </section>
        </div>
      )}

      {peekReqOpen && (
        <Modal
          title="Org-admin consent required"
          onClose={() => !peekReqBusy && doCancelPeekRequest()}
        >
          <p className="text-sm text-slate-400">
            Viewing transcripts in this organization requires approval from the
            org admin. A confirmation link is emailed to their registered
            account — once they click "I Confirm", the transcript unlocks
            automatically.
          </p>
          {!peekReqId && (
            <div className="mt-4 flex items-center gap-2">
              <button
                onClick={() => void doRequestPeekConsent()}
                disabled={peekReqBusy}
                className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
              >
                {peekReqBusy ? "Sending…" : "Send consent request to org admin"}
              </button>
            </div>
          )}
          {peekReqId && (
            <div className="mt-4 space-y-2">
              <p className="text-xs text-slate-400">
                Confirmation link sent to{" "}
                <span className="font-mono">{peekReqSentTo}</span> — waiting
                for the org admin to approve…
              </p>
              {peekReqStatus === "expired" && (
                <p className="text-xs text-amber-300">
                  This request expired — send a new one if access is still
                  needed.
                </p>
              )}
              <div className="flex gap-2 pt-1">
                {peekReqStatus === "expired" ? (
                  <button
                    onClick={() => void doRequestPeekConsent()}
                    disabled={peekReqBusy}
                    className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
                  >
                    Send new request
                  </button>
                ) : (
                  <button
                    onClick={doCancelPeekRequest}
                    disabled={peekReqBusy}
                    className="rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                  >
                    Cancel
                  </button>
                )}
              </div>
            </div>
          )}
        </Modal>
      )}

      {peekOpen && (
        <Modal title="Conversation transcript" onClose={() => setPeekOpen(false)}>
          {peeking || !peekResult ? (
            <p className="py-8 text-center text-sm text-slate-400">Loading…</p>
          ) : (
            <div className="space-y-3">
              {!revealed && (
                <div className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
                  PII is masked. Emails, phones and customer names in this
                  transcript are hidden by default.
                </div>
              )}
              <div className="rounded-md border border-slate-800 bg-slate-900 p-3 text-xs">
                <p>
                  <span className="text-slate-400">Session:</span>{" "}
                  <span className="font-mono">{peekResult.session_id}</span>
                </p>
                <p>
                  <span className="text-slate-400">Channel:</span>{" "}
                  {peekResult.channel} ·{" "}
                  <span className="font-mono">{peekResult.channel_user_id}</span>
                </p>
                <p>
                  <span className="text-slate-400">Status:</span>{" "}
                  {peekResult.session_status}
                </p>
              </div>
              {peekResult.messages.length === 0 ? (
                <p className="py-6 text-center text-sm text-slate-500">
                  No messages in this session.
                </p>
              ) : (
                <div className="max-h-96 space-y-2 overflow-y-auto">
                  {peekResult.messages.map((m) => (
                    <div
                      key={m.id}
                      className={`rounded-lg border p-3 text-sm ${
                        m.role === "user"
                          ? "border-indigo-800 bg-indigo-950/30"
                          : "border-slate-800 bg-slate-900"
                      }`}
                    >
                      <p className="text-xs text-slate-400">
                        <span className="font-mono">{m.sender_type}</span> ·{" "}
                        {new Date(m.created_at).toLocaleString()}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap text-slate-100">
                        {m.content}
                      </p>
                    </div>
                  ))}
                </div>
              )}
              {canUnmask && !revealed && (
                <button
                  onClick={() => void openReveal()}
                  className="rounded-md border border-amber-700 bg-amber-950/40 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-950"
                >
                  Reveal full transcript (MFA + reason)
                </button>
              )}
              {revealed && (
                <p className="text-xs text-amber-400/80">
                  Unmasked — this reveal was audit-logged with your stated
                  reason.
                </p>
              )}
            </div>
          )}
        </Modal>
      )}

      {unmaskReqOpen && (
        <Modal
          title="Org-admin approval required for PII reveal"
          onClose={() => !unmaskReqBusy && setUnmaskReqOpen(false)}
        >
          <p className="text-sm text-slate-400">
            Releasing full customer details (emails, phones, names) requires a
            separate approval from the org admin — the approval to view the
            transcript is not enough. A confirmation link is emailed to their
            registered account.
          </p>
          {!unmaskReqId && (
            <div className="mt-4 flex items-center gap-2">
              <button
                onClick={() => void doRequestUnmaskConsent()}
                disabled={unmaskReqBusy}
                className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
              >
                {unmaskReqBusy ? "Sending…" : "Send approval request to org admin"}
              </button>
            </div>
          )}
          {unmaskReqId && (
            <div className="mt-4 space-y-2">
              <p className="text-xs text-slate-400">
                Approval link sent to{" "}
                <span className="font-mono">{unmaskReqSentTo}</span> — waiting
                for the org admin to approve…
              </p>
              {unmaskReqStatus === "expired" && (
                <p className="text-xs text-amber-300">
                  This request expired — send a new one if access is still
                  needed.
                </p>
              )}
              <div className="flex gap-2 pt-1">
                {unmaskReqStatus === "expired" ? (
                  <button
                    onClick={() => void doRequestUnmaskConsent()}
                    disabled={unmaskReqBusy}
                    className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
                  >
                    Send new request
                  </button>
                ) : (
                  <button
                    onClick={() => setUnmaskReqOpen(false)}
                    disabled={unmaskReqBusy}
                    className="rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                  >
                    Cancel
                  </button>
                )}
              </div>
            </div>
          )}
        </Modal>
      )}

      {revealOpen && (
        <Modal
          title="Reveal full transcript"
          onClose={() => !revealBusy && setRevealOpen(false)}
        >
          <p className="text-sm text-slate-400">
            Viewing unmasked customer PII is audit-logged with your identity and
            reason. Re-enter your 6-digit TOTP code to confirm.
          </p>
          <textarea
            value={revealReason}
            onChange={(e) => setRevealReason(e.target.value)}
            placeholder="Reason (min 10 characters) — e.g. investigating a billing dispute for this org"
            rows={3}
            className="mt-3 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600"
          />
          <input
            value={revealCode}
            onChange={(e) => setRevealCode(e.target.value)}
            placeholder="000000"
            inputMode="numeric"
            className="mt-2 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-center font-mono text-lg text-slate-100"
          />
          <div className="mt-4 flex justify-end gap-2">
            <button
              onClick={() => setRevealOpen(false)}
              disabled={revealBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={() => void revealPeek()}
              disabled={
                revealBusy || revealCode.length !== 6 || revealReason.trim().length < 10
              }
              className="rounded-md bg-amber-700 px-4 py-2 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-40"
            >
              {revealBusy ? "Revealing…" : "Confirm & reveal"}
            </button>
          </div>
        </Modal>
      )}

      {stepupOpen && (
        <Modal
          title="Confirm with MFA"
          onClose={() => !stepupBusy && setStepupOpen(false)}
        >
          <p className="text-sm text-slate-400">
            Impersonation requires a fresh MFA confirmation. Enter your 6-digit
            TOTP code.
          </p>
          <input
            value={stepupCode}
            onChange={(e) => setStepupCode(e.target.value)}
            placeholder="000000"
            inputMode="numeric"
            className="mt-3 w-full rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-center font-mono text-lg text-slate-100"
          />
          <div className="mt-4 flex justify-end gap-2">
            <button
              onClick={() => setStepupOpen(false)}
              disabled={stepupBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={() => void startImpersonation()}
              disabled={stepupBusy || stepupCode.length !== 6}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-40"
            >
              {stepupBusy ? "Verifying…" : "Confirm & start"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
