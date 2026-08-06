"use client";

import { useCallback, useEffect, useState } from "react";
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

export default function SupportPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const canImpersonate = can("support.impersonate");
  const canPeek = can("support.conversations.view");
  const canUnmask = can("pii.unmask");

  // Peek PII
  const [peekUnmask, setPeekUnmask] = useState(false);

  // Impersonation
  const [orgSearch, setOrgSearch] = useState("");
  const [orgOptions, setOrgOptions] = useState<OrgRow[]>([]);
  const [selectedOrg, setSelectedOrg] = useState<OrgRow | null>(null);
  const [users, setUsers] = useState<UserRow[] | null>(null);
  const [selectedUser, setSelectedUser] = useState<UserRow | null>(null);
  const [usersLoading, setUsersLoading] = useState(false);
  const [stepupOpen, setStepupOpen] = useState(false);
  const [stepupCode, setStepupCode] = useState("");
  const [stepupBusy, setStepupBusy] = useState(false);
  const [issuing, setIssuing] = useState(false);
  const [issuedToken, setIssuedToken] = useState<{ token: string; expires_at: string; org_name: string; user_email: string } | null>(null);

  // Peek
  const [peekOrg, setPeekOrg] = useState<OrgRow | null>(null);
  const [peekSearch, setPeekSearch] = useState("");
  const [peekOrgOptions, setPeekOrgOptions] = useState<OrgRow[]>([]);
  const [conversations, setConversations] = useState<Conversation[] | null>(null);
  const [conversationsLoading, setConversationsLoading] = useState(false);
  const [peekOpen, setPeekOpen] = useState(false);
  const [peeking, setPeeking] = useState(false);
  const [peekResult, setPeekResult] = useState<PeekResult | null>(null);

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
  };

  const doStepupAndStart = async () => {
    if (!selectedOrg || !selectedUser || !stepupCode.trim()) return;
    setStepupBusy(true);
    try {
      await api.post("/auth/mfa/stepup", { code: stepupCode.trim() });
      setStepupOpen(false);
      setIssuing(true);
      const r = await api.post<{
        token: string;
        expires_at: string;
        org_name: string;
        user_email: string;
      }>("/impersonation/start", {
        org_id: selectedOrg.id,
        user_email: selectedUser.email,
      });
      setIssuedToken(r);
      toast("success", `Impersonating ${r.user_email}`);
      void loadHistory();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Step-up or start failed");
    } finally {
      setStepupBusy(false);
      setIssuing(false);
    }
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
        `/support/orgs/${org.id}/conversations${peekUnmask && canUnmask ? "?unmask=true" : ""}`
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
    setPeekOpen(true);
    setPeeking(true);
    setPeekResult(null);
    try {
      const r = await api.get<PeekResult>(
        `/support/orgs/${peekOrg.id}/conversations/${c.id}/messages${peekUnmask && canUnmask ? "?unmask=true" : ""}`
      );
      setPeekResult(r);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to load transcript");
      setPeekOpen(false);
    } finally {
      setPeeking(false);
    }
  };

  const copyToken = () => {
    if (issuedToken) {
      void navigator.clipboard.writeText(issuedToken.token);
      toast("success", "Token copied — it is shown only once");
    }
  };

  const statusBadge = (s: string) =>
    s === "active"
      ? "bg-green-950/60 text-green-300"
      : "bg-slate-800 text-slate-400";

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold">Support</h1>
        <p className="mt-1 text-sm text-slate-400">
          Impersonate org admins (MFA step-up required, fully audited) and peek
          into conversations with PII masked before display.
        </p>
      </div>

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
            <div className="mt-4 rounded-md border border-amber-700/60 bg-amber-950/30 p-4 text-sm">
              <p className="font-semibold text-amber-300">
                Active impersonation token
              </p>
              <p className="mt-1 text-amber-200">
                {issuedToken.user_email} at {issuedToken.org_name} — expires{" "}
                {fmtDate(issuedToken.expires_at)}.
              </p>
              <p className="mt-2 text-xs text-amber-300">
                Token is shown only once. Copy it to use the tenant API as this
                user:
              </p>
              <div className="mt-1 flex items-center gap-2">
                <code className="flex-1 truncate rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-amber-100">
                  {issuedToken.token}
                </code>
                <button
                  onClick={copyToken}
                  className="rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800"
                >
                  Copy
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
                default.
              </p>
            </div>
            {canUnmask && (
              <button
                onClick={() => setPeekUnmask((v) => !v)}
                className={`rounded-md border px-3 py-1.5 text-sm ${
                  peekUnmask
                    ? "border-amber-700 bg-amber-950/40 text-amber-300"
                    : "border-slate-700 text-slate-300 hover:bg-slate-800"
                }`}
              >
                {peekUnmask ? "Unmasked" : "Unmask PII"}
              </button>
            )}
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
            minutes and is required before impersonating.
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
              {stepupBusy ? "Verifying…" : "Verify & impersonate"}
            </button>
          </div>
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
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}
