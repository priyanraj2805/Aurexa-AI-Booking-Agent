"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal } from "@/components/modal";

type OrgRow = { id: string; name: string; business_name: string | null };
type UserRow = { id: string; email: string; full_name: string | null; is_active: boolean };
type ImpSession = {
  id: string;
  org_name: string;
  user_email: string;
  status: string;
  started_at: string;
  expires_at: string;
  ended_at: string | null;
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
type PeekMessage = { id: string; role: string; content: string; sender_type: string; created_at: string };
type PeekResult = {
  session_id: string;
  channel: string;
  channel_user_id: string | null;
  session_status: string;
  state_data: Record<string, unknown>;
  messages: PeekMessage[];
};

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");

export default function ImpersonationPanel({
  initialRequestId,
  requestNonce,
}: {
  initialRequestId?: string;
  requestNonce?: string;
}) {
  const { can } = useAuth();
  const { toast } = useToast();

  const canImpersonate = can("support.impersonate");
  const canPeek = can("support.conversations.view");
  const canUnmask = can("pii.unmask");

  // Impersonation
  const [orgSearch, setOrgSearch] = useState("");
  const [orgOptions, setOrgOptions] = useState<OrgRow[]>([]);
  const [selectedOrg, setSelectedOrg] = useState<OrgRow | null>(null);
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [selectedUser, setSelectedUser] = useState<UserRow | null>(null);
  const [usersLoading, setUsersLoading] = useState(false);
  const [stepupOpen, setStepupOpen] = useState(false);
  const [stepupCode, setStepupCode] = useState("");
  const [stepupMode, setStepupMode] = useState<"start" | "confirm">("start");
  const [stepupBusy, setStepupBusy] = useState(false);
  const [issuing, setIssuing] = useState(false);
  const [pendingReq, setPendingReq] = useState<{
    id: string;
    org_id: string | null;
    org_name: string;
    user_email: string;
    status: string;
    link_sent_to: string | null;
    link_expires_at: string | null;
    link_ttl_hours: number | null;
  } | null>(null);
  const [resending, setResending] = useState(false);
  const [pendingBusy, setPendingBusy] = useState(false);
  const [issuedToken, setIssuedToken] = useState<{
    session_active: boolean;
    console_url: string;
    expires_at: string;
    org_name: string;
    user_email: string;
  } | null>(null);

  // Peek
  const [peekOrg, setPeekOrg] = useState<OrgRow | null>(null);
  const [peekSearch, setPeekSearch] = useState("");
  const [peekOrgOptions, setPeekOrgOptions] = useState<OrgRow[]>([]);
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [peekOpen, setPeekOpen] = useState(false);
  const [peeking, setPeeking] = useState(false);
  const [peekResult, setPeekResult] = useState<PeekResult | null>(null);
  const [peekRevealed, setPeekRevealed] = useState(false);
  const [revealOpen, setRevealOpen] = useState(false);
  const [revealCode, setRevealCode] = useState("");
  const [revealReason, setRevealReason] = useState("");
  const [revealBusy, setRevealBusy] = useState(false);
  const [peekReqOpen, setPeekReqOpen] = useState(false);
  const [peekReqBusy, setPeekReqBusy] = useState(false);
  const [peekReqId, setPeekReqId] = useState<string | null>(null);
  const [peekReqSentTo, setPeekReqSentTo] = useState("");
  const [peekReqStatus, setPeekReqStatus] = useState<string | null>(null);
  const [pendingPeek, setPendingPeek] = useState<Conversation | null>(null);

  // History
  const [history, setHistory] = useState<ImpSession[] | null>(null);
  const [stoppingId, setStoppingId] = useState<string | null>(null);

  const loadHistory = useCallback(async () => {
    if (!canImpersonate) return;
    try {
      setHistory(await api.get<ImpSession[]>("/impersonation"));
    } catch {
      setHistory([]);
    }
  }, [canImpersonate]);

  useEffect(() => {
    void loadHistory();
    window.addEventListener("impersonation:changed", loadHistory);
    return () => window.removeEventListener("impersonation:changed", loadHistory);
  }, [loadHistory]);

  // Restore an in-flight impersonation after a page refresh: the console_url
  // is re-minted by GET /impersonation/active (fresh single-use handshake).
  useEffect(() => {
    if (!canImpersonate) return;
    let cancelled = false;
    (async () => {
      try {
        const a = await api.get<{
          id: string;
          org_name: string;
          user_email: string;
          started_at: string;
          expires_at: string;
          console_url: string;
        } | null>("/impersonation/active");
        if (!cancelled && a) {
          setIssuedToken({
            session_active: true,
            console_url: a.console_url,
            expires_at: a.expires_at,
            org_name: a.org_name,
            user_email: a.user_email,
          });
        }
      } catch {
        // no active session (or transient) — nothing to restore
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [canImpersonate]);

  const searchOrgs = async () => {
    try {
      const res = await api.get<{ items: OrgRow[] }>(
        `/tenants/orgs?search=${encodeURIComponent(orgSearch)}&page_size=20`
      );
      setOrgOptions(res.items);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Organization search failed");
    }
  };

  const pickOrg = async (org: OrgRow) => {
    setSelectedOrg(org);
    setSelectedUser(null);
    setUsers(null);
    setUsersLoading(true);
    try {
      const res = await api.get<{ items: UserRow[] }>(
        `/tenants/orgs/${org.id}/users?page_size=100&unmask=true`
      );
      setUsers(res.items.filter((u) => u.is_active));
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to load users");
    } finally {
      setUsersLoading(false);
    }
  };

  const beginStepup = () => {
    setStepupCode("");
    setStepupOpen(true);
    setStepupMode("start");
  };

  const doStepupAndStart = async () => {
    if (!stepupCode.trim()) return;
    if (stepupMode === "start" && (!selectedOrg || !selectedUser)) return;
    setStepupBusy(true);
    try {
      await api.post("/auth/mfa/stepup", { code: stepupCode.trim() });
      setStepupOpen(false);
      if (stepupMode === "confirm") {
        await doConfirmAfterStepup();
        setStepupBusy(false);
        return;
      }
      if (!selectedOrg || !selectedUser) return;
      const org = selectedOrg;
      const user = selectedUser;
      setIssuing(true);
      const r = await api.post<{
        id: string;
        org_name: string;
        user_email: string;
        status: string;
        link_sent_to: string;
        link_expires_at: string;
        link_ttl_hours: number;
      }>("/impersonation/start", {
        org_id: org.id,
        user_email: user.email,
      });
      setPendingReq({
        id: r.id,
        org_id: org.id,
        org_name: r.org_name,
        user_email: r.user_email,
        status: r.status,
        link_sent_to: r.link_sent_to,
        link_expires_at: r.link_expires_at,
        link_ttl_hours: r.link_ttl_hours,
      });
      toast("info", `Confirmation link sent to ${r.link_sent_to}`);
      void loadHistory();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Step-up or request failed");
    } finally {
      setStepupBusy(false);
      setIssuing(false);
    }
  };

  const doResendLink = async () => {
    if (!pendingReq) return;
    setResending(true);
    try {
      const r = await api.post<{
        id: string;
        org_name: string;
        user_email: string;
        status: string;
        link_sent_to: string;
        link_expires_at: string;
        link_ttl_hours: number;
      }>("/impersonation/start", {
        org_id: pendingReq.org_id ?? selectedOrg?.id,
        user_email: pendingReq.user_email,
      });
      setPendingReq({
        ...pendingReq,
        id: r.id,
        org_name: r.org_name,
        status: r.status,
        link_sent_to: r.link_sent_to,
        link_expires_at: r.link_expires_at,
        link_ttl_hours: r.link_ttl_hours,
      });
      toast("info", `Fresh confirmation link sent to ${r.link_sent_to}`);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to resend link");
    } finally {
      setResending(false);
    }
  };

  const doConfirmAfterStepup = async () => {
    if (!pendingReq) return;
    setPendingBusy(true);
    try {
      const r = await api.post<{
        id: string;
        expires_at: string;
        org_name: string;
        user_email: string;
        session_active: boolean;
        console_url: string;
      }>(`/impersonation/${pendingReq.id}/confirm`, {});
      setIssuedToken(r);
      setPendingReq(null);
      toast("success", `Impersonating ${r.user_email}`);
      void loadHistory();
      window.dispatchEvent(new Event("impersonation:changed"));
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Confirmation failed");
    } finally {
      setPendingBusy(false);
    }
  };

  const loadRequestIntoPanel = useCallback(async (id: string, notify = true) => {
    try {
      const r = await api.get<{
        id: string;
        org_id: string;
        org_name: string;
        user_email: string;
        status: string;
        expires_at: string | null;
        ended_at: string | null;
      }>(`/impersonation/${id}`);
      if (r.status === "active") {
        if (notify) toast("info", `Session for ${r.user_email} is already active`);
        return;
      }
      if (r.status !== "approved") {
        if (notify) {
          const friendly: Record<string, string> = {
            ended: `The impersonation session for ${r.user_email} has ended — you cannot impersonate them now.`,
            expired: `The approval for ${r.user_email} has expired. Send a new request if access is still needed.`,
            rejected: `The org admin declined the request for ${r.user_email} — no session was started.`,
            pending: `The request for ${r.user_email} is still awaiting the org admin's confirmation.`,
          };
          toast("info", friendly[r.status] ?? `Request is ${r.status} — it cannot be started`);
        }
        return;
      }
      setPendingReq({
        id: r.id,
        org_id: r.org_id,
        org_name: r.org_name,
        user_email: r.user_email,
        status: r.status,
        link_sent_to: null,
        link_expires_at: null,
        link_ttl_hours: null,
      });
      window.setTimeout(() => {
        document
          .getElementById("impersonation-action-card")
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      }, 50);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to load request");
    }
  }, [toast]);

  const loadedKeyRef = useRef("");

  useEffect(() => {
    if (!initialRequestId) return;
    const key = `${initialRequestId}:${requestNonce ?? ""}`;
    if (loadedKeyRef.current === key) return;
    loadedKeyRef.current = key;
    const flagged = window.sessionStorage.getItem("imp:toast") === initialRequestId;
    if (flagged) window.sessionStorage.removeItem("imp:toast");
    void loadRequestIntoPanel(initialRequestId, flagged);
  }, [initialRequestId, requestNonce, loadRequestIntoPanel]);

  useEffect(() => {
    if (!pendingReq || pendingReq.status === "approved" || pendingReq.status === "active") {
      return;
    }
    const t = setInterval(async () => {
      try {
        const r = await api.get<{ id: string; org_name: string; user_email: string; status: string; approved_at: string | null; rejected_at: string | null }>(
          `/impersonation/${pendingReq.id}`
        );
        setPendingReq((prev) =>
          prev
            ? {
                ...prev,
                status: r.status,
              }
            : prev
        );
        if (r.status === "approved") {
          toast("success", `Org admin confirmed — impersonation approved for ${r.user_email}`);
          void loadHistory();
        }
        if (r.status === "rejected" || r.status === "expired") {
          toast("error", `Impersonation request ${r.status}`);
          void loadHistory();
        }
        if (r.status !== "approved") {
          clearInterval(t);
        }
      } catch {
        // transient; keep polling
      }
    }, 4000);
    return () => clearInterval(t);
  }, [pendingReq, toast, loadHistory]);

  const beginConfirmStepup = () => {
    setStepupCode("");
    setStepupOpen(true);
    setStepupMode("confirm");
  };

  const stopSession = async (id: string) => {
    setStoppingId(id);
    try {
      await api.post(`/impersonation/${id}/stop`);
      toast("success", "Impersonation ended");
      void loadHistory();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Stop failed");
    } finally {
      setStoppingId(null);
    }
  };

  const searchPeekOrgs = async () => {
    try {
      const res = await api.get<{ items: OrgRow[] }>(
        `/tenants/orgs?search=${encodeURIComponent(peekSearch)}&page_size=20`
      );
      setPeekOrgOptions(res.items);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Organization search failed");
    }
  };

  const loadConversations = async (org: OrgRow) => {
    setPeekOrg(org);
    setConversations(null);
    setConversationsLoading(true);
    try {
      const res = await api.get<{ items: Conversation[] }>(
        `/support/orgs/${org.id}/conversations`
      );
      setConversations(res.items);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to load conversations");
    } finally {
      setConversationsLoading(false);
    }
  };

  const openPeek = async (c: Conversation) => {
    if (!peekOrg) return;
    setPendingPeek(c);
    try {
      const g = await api.get<{ granted: boolean }>(
        `/support/peek/grant?org_id=${peekOrg.id}`
      );
      if (g.granted) {
        void doLoadTranscript(c);
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

  const doLoadTranscript = async (c: Conversation) => {
    if (!peekOrg) return;
    setPeekOpen(true);
    setPeeking(true);
    setPeekResult(null);
    setPeekRevealed(false);
    try {
      const r = await api.get<PeekResult>(
        `/support/orgs/${peekOrg.id}/conversations/${c.id}/messages`
      );
      setPeekResult(r);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to load transcript");
      setPeekOpen(false);
    } finally {
      setPeeking(false);
    }
  };

  const doRequestPeekConsent = async () => {
    if (!peekOrg) return;
    setPeekReqBusy(true);
    try {
      const r = await api.post<{
        id: string;
        link_sent_to: string;
        link_expires_at: string;
        link_ttl_hours: number;
      }>("/support/peek/request", { org_id: peekOrg.id });
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
          if (pendingPeek) void doLoadTranscript(pendingPeek);
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
  }, [peekReqOpen, peekReqId, pendingPeek]);

  const revealPeek = async () => {
    if (!peekResult || !peekOrg) return;
    setRevealBusy(true);
    try {
      await api.post("/auth/mfa/stepup", { code: revealCode });
      const r = await api.get<PeekResult>(
        `/support/orgs/${peekOrg.id}/conversations/${peekResult.session_id}/messages?unmask=true&reason=${encodeURIComponent(revealReason.trim())}`
      );
      setPeekResult(r);
      setPeekRevealed(true);
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

  useEffect(() => {
    if (!issuedToken) return;
    const t = setInterval(async () => {
      try {
        const a = await api.get<{
          id: string;
          org_name: string;
          user_email: string;
          started_at: string;
          expires_at: string;
          console_url: string;
        } | null>("/impersonation/active");
        if (!a) return;
        setIssuedToken((prev) =>
          prev
            ? {
                ...prev,
                org_name: a.org_name,
                user_email: a.user_email,
                expires_at: a.expires_at,
                console_url: a.console_url,
              }
            : prev
        );
      } catch {
        // transient; keep polling
      }
    }, 20000);
    return () => clearInterval(t);
  }, [issuedToken, api]);

  const copyToken = () => {
    if (issuedToken?.console_url) {
      void navigator.clipboard.writeText(issuedToken.console_url);
      toast("success", "Console link copied");
    }
  };

  const statusBadge = (s: string) =>
    s === "active"
      ? "bg-green-950/60 text-green-300"
      : s === "pending"
        ? "bg-sky-950/60 text-sky-300"
        : s === "approved"
          ? "bg-indigo-950/60 text-indigo-300"
          : s === "expired"
            ? "bg-amber-950/60 text-amber-300"
            : s === "rejected"
              ? "bg-red-950/60 text-red-300"
              : "bg-slate-800 text-slate-400";

  return (
    <div className="space-y-8">
      {canImpersonate && (
        <section className="rounded-lg border border-slate-800 p-5">
          <h2 className="text-lg font-semibold">Impersonate a user</h2>
          <p className="mt-1 text-sm text-slate-400">
            Acts as the user inside their org for up to 30 minutes. Starting a
            new session ends any active one.
          </p>

          <div className="mt-4 flex items-end gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Search organization
              </label>
              <div className="flex gap-2">
                <input
                  value={orgSearch}
                  onChange={(e) => setOrgSearch(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void searchOrgs()}
                  placeholder="org name…"
                  className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
                <button
                  onClick={searchOrgs}
                  className="rounded-md border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800"
                >
                  Search
                </button>
              </div>
            </div>
            <div className="w-72">
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Pick org
              </label>
              <select
                aria-label="Organization to impersonate"
                value={selectedOrg?.id ?? ""}
                onChange={(e) => {
                  const org = orgOptions.find((o) => o.id === e.target.value);
                  if (org) void pickOrg(org);
                }}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              >
                <option value="">Select…</option>
                {orgOptions.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {selectedOrg && (
            <div className="mt-4 w-72">
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                User to impersonate
              </label>
              <select
                aria-label="User to impersonate"
                value={selectedUser?.id ?? ""}
                onChange={(e) =>
                  setSelectedUser(
                    users?.find((u) => u.id === e.target.value) ?? null
                  )
                }
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              >
                <option value="">Select…</option>
                {usersLoading && <option>Loading…</option>}
                {(users ?? []).map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.full_name ? `${u.full_name} — ` : ""}
                    {u.email}
                  </option>
                ))}
              </select>
            </div>
          )}

          {issuedToken && (
            <div className="mt-4 rounded-md border border-green-700/60 bg-green-950/30 p-4 text-sm">
              <p className="font-semibold text-green-300">
                Active impersonation session
              </p>
              <p className="mt-1 text-green-200">
                {issuedToken.user_email} at {issuedToken.org_name} — expires{" "}
                {fmtDate(issuedToken.expires_at)}.
              </p>
              <p className="mt-2 text-xs text-green-300">
                You can open the user&apos;s admin console. Your identity is
                shown to them and every action is audited.
              </p>
              <div className="mt-3 flex items-center gap-2">
                <a
                  href={issuedToken.console_url}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-md bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700"
                >
                  Open console as {issuedToken.user_email}
                </a>
                <button
                  onClick={copyToken}
                  className="rounded border border-slate-700 px-2 py-1.5 text-xs hover:bg-slate-800"
                >
                  Copy link
                </button>
              </div>
            </div>
          )}

          <div className="mt-5 flex gap-2">
            <button
              onClick={beginStepup}
              disabled={!selectedOrg || !selectedUser}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
            >
              {issuing ? "Starting…" : "Verify MFA & impersonate"}
            </button>
{pendingReq && (
            <div
              id="impersonation-action-card"
              className="mt-4 rounded-md border border-sky-700/60 bg-sky-950/30 p-4 text-sm"
            >
              <p className="font-semibold text-sky-300">
                Consent request sent to{" "}
                <span className="font-mono">{pendingReq.user_email}</span>
              </p>
              <p className="mt-1 text-sky-200">
                The org admin must verify before the impersonation token is
                issued. Status:{" "}
                <span className="font-semibold uppercase">
                  {pendingReq.status === "approved"
                    ? "Approved — confirm to start"
                    : pendingReq.status}
                </span>
              </p>
              {pendingReq.status === "pending" && (
                <div className="mt-2">
                  <p className="text-xs text-sky-300">
                    A confirmation link was emailed to the org admin
                    (registered account) and is valid for{" "}
                    {pendingReq.link_ttl_hours ?? 24} hours. Ask them to open
                    it and click &quot;I Confirm&quot; to approve this request.
                    The request is automatically approved only after they
                    confirm.
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => void doResendLink()}
                      disabled={resending}
                      className="rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                    >
                      {resending ? "Sending…" : "Resend link"}
                    </button>
                  </div>
                  {pendingReq.link_sent_to && (
                    <p className="mt-2 text-xs text-sky-300/70">
                      Link sent to{" "}
                      <span className="font-mono">{pendingReq.link_sent_to}</span>
                      {pendingReq.link_expires_at && (
                        <>
                          {" "}
                          — expires {fmtDate(pendingReq.link_expires_at)}
                        </>
                      )}
                    </p>
                  )}
                </div>
              )}
              {pendingReq.status === "approved" && (
                <div className="mt-3">
                  <button
                    onClick={beginConfirmStepup}
                    disabled={pendingBusy}
                    className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
                  >
                    {pendingBusy ? "Starting…" : "Org approved — start session"}
                  </button>
                  <p className="mt-1 text-xs text-sky-300">
                    Re-enter your MFA code to confirm (approval may be old).
                  </p>
                </div>
              )}
              {pendingReq.status === "rejected" && (
                <p className="mt-2 text-red-300">
                  The org admin declined this request. No token was issued.
                </p>
              )}
              {pendingReq.status === "expired" && (
                <p className="mt-2 text-red-300">
                  The confirmation link expired. Create a new request.
                </p>
              )}
            </div>
          )}

          {issuedToken && (
              <button
                onClick={() => setIssuedToken(null)}
                className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800"
              >
                Dismiss
              </button>
            )}
          </div>
        </section>
      )}

      {canPeek && (
        <section className="rounded-lg border border-slate-800 p-5">
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-lg font-semibold">Conversation peek</h2>
              <p className="mt-1 text-sm text-slate-400">
                Read-only transcripts. Emails, phones and names are masked by
                default; revealing PII requires MFA and a logged reason.
              </p>
            </div>
          </div>

          <div className="mt-4 flex items-end gap-3">
            <div className="flex-1">
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Search organization
              </label>
              <div className="flex gap-2">
                <input
                  value={peekSearch}
                  onChange={(e) => setPeekSearch(e.target.value)}
                  onKeyDown={(e) =>
                    e.key === "Enter" && void searchPeekOrgs()
                  }
                  placeholder="org name…"
                  className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
                <button
                  onClick={searchPeekOrgs}
                  className="rounded-md border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800"
                >
                  Search
                </button>
              </div>
            </div>
            <div className="w-72">
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Pick org
              </label>
              <select
                aria-label="Organization to view conversations"
                value={peekOrg?.id ?? ""}
                onChange={(e) => {
                  const org = peekOrgOptions.find((o) => o.id === e.target.value);
                  if (org) void loadConversations(org);
                }}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              >
                <option value="">Select…</option>
                {peekOrgOptions.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {peekOrg && (
            <div className="mt-4 overflow-x-auto rounded-lg border border-slate-800">
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
                  {conversationsLoading && (
                    <tr>
                      <td colSpan={7} className="px-4 py-3 text-slate-500">
                        Loading…
                      </td>
                    </tr>
                  )}
                  {(conversations ?? []).map((c) => (
                    <tr key={c.id}>
                      <td className="px-4 py-3">{c.channel}</td>
                      <td className="px-4 py-3 font-mono text-xs">
                        {c.channel_user_id ?? "—"}
                      </td>
                      <td className="px-4 py-3">{c.session_status}</td>
                      <td className="px-4 py-3 text-slate-400">
                        {c.current_intent ?? "—"}
                      </td>
                      <td className="px-4 py-3">{c.message_count}</td>
                      <td className="px-4 py-3 text-xs text-slate-500">
                        {fmtDate(c.updated_at)}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => void openPeek(c)}
                          className="rounded border border-slate-700 px-2 py-1 text-xs text-indigo-300 hover:bg-slate-800"
                        >
                          View transcript
                        </button>
                      </td>
                    </tr>
                  ))}
                  {!conversationsLoading && conversations?.length === 0 && (
                    <tr>
                      <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                        No conversations in this org
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}

      {canImpersonate && (
        <section className="rounded-lg border border-slate-800 p-5">
          <h2 className="text-lg font-semibold">Impersonation history</h2>
          <div className="mt-4 overflow-x-auto rounded-lg border border-slate-800">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-4 py-3">Org</th>
                  <th className="px-4 py-3">User</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Started</th>
                  <th className="px-4 py-3">Expires</th>
                  <th className="px-4 py-3">Ended</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800 bg-slate-900/50">
                {(history ?? []).map((s) => (
                  <tr key={s.id}>
                    <td className="px-4 py-3">{s.org_name}</td>
                    <td className="px-4 py-3">{s.user_email}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs font-semibold ${statusBadge(s.status)}`}
                      >
                        {s.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {fmtDate(s.started_at)}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {fmtDate(s.expires_at)}
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {fmtDate(s.ended_at)}
                    </td>
                    <td className="px-4 py-3">
                      {s.status === "active" && (
                        <button
                          onClick={() => void stopSession(s.id)}
                          disabled={stoppingId === s.id}
                          className="rounded border border-red-900 px-2 py-1 text-xs text-red-400 hover:bg-red-950 disabled:opacity-50"
                        >
                          {stoppingId === s.id ? "Ending…" : "End now"}
                        </button>
                      )}
                      {s.status === "approved" && (
                        <button
                          onClick={() => void loadRequestIntoPanel(s.id)}
                          className="rounded border border-indigo-700 px-2 py-1 text-xs text-indigo-300 hover:bg-indigo-950"
                        >
                          Start session
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
                {history && history.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                      No impersonation sessions yet
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {stepupOpen && (
        <Modal title="MFA step-up" onClose={() => !stepupBusy && setStepupOpen(false)}>
          <p className="text-sm text-slate-400">
            Re-verify with your authenticator code. Step-up is valid for a few
            minutes
            {stepupMode === "confirm"
              ? " and is required before starting the approved impersonation session."
              : " and is required before impersonating."}
          </p>
          <input
            autoFocus
            value={stepupCode}
            onChange={(e) => setStepupCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
            onKeyDown={(e) => e.key === "Enter" && void doStepupAndStart()}
            placeholder="000000"
            inputMode="numeric"
            className="mt-4 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-lg tracking-widest outline-none focus:border-indigo-500"
          />
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setStepupOpen(false)}
              disabled={stepupBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={doStepupAndStart}
              disabled={stepupBusy || stepupCode.length < 6}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {stepupBusy
                ? "Verifying…"
                : stepupMode === "confirm"
                  ? "Verify & start session"
                  : "Verify & impersonate"}
            </button>
          </div>
        </Modal>
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
        <Modal
          title={peekResult ? `Transcript — ${peekResult.channel_user_id ?? peekResult.session_id}` : "Loading transcript…"}
          onClose={() => setPeekOpen(false)}
        >
          {peeking && <p className="text-sm text-slate-400">Loading…</p>}
          {peekResult && (
            <div className="max-h-[60vh] space-y-4 overflow-y-auto pr-1">
              {!peekRevealed && (
                <div className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-400">
                  PII is masked. Emails, phones and customer names in this
                  transcript are hidden by default.
                </div>
              )}
              <div className="rounded-md border border-slate-800 p-3 text-xs text-slate-400">
                <p>
                  <strong className="text-slate-200">Status:</strong>{" "}
                  {peekResult.session_status}
                </p>
                <p>
                  <strong className="text-slate-200">State:</strong>{" "}
                  {Object.keys(peekResult.state_data).length > 0
                    ? JSON.stringify(peekResult.state_data)
                    : "—"}
                </p>
              </div>
              {peekResult.messages.map((m) => (
                <div
                  key={m.id}
                  className={`max-w-[85%] rounded-lg border px-3 py-2 text-sm ${
                    m.sender_type === "user"
                      ? "ml-auto border-indigo-800 bg-indigo-950/40"
                      : "border-slate-800 bg-slate-900"
                  }`}
                >
                  <p className="text-xs text-slate-500">
                    {m.role} · {fmtDate(m.created_at)}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap text-slate-200">
                    {m.content}
                  </p>
                </div>
              ))}
              {canUnmask && !peekRevealed && (
                <button
                  onClick={() => setRevealOpen(true)}
                  className="rounded-md border border-amber-700 bg-amber-950/40 px-3 py-1.5 text-xs font-medium text-amber-300 hover:bg-amber-950"
                >
                  Reveal full transcript (MFA + reason)
                </button>
              )}
              {peekRevealed && (
                <p className="text-xs text-amber-400/80">
                  Unmasked — this reveal was audit-logged with your stated
                  reason.
                </p>
              )}
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
    </div>
  );
}