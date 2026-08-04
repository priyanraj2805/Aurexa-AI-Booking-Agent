"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { ConfirmDialog, Modal } from "@/components/modal";

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
  const [tabError, setTabError] = useState<string | null>(null);

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
        } else if (t === "Audit") {
          const a = await api.get<{ items: AuditRow[] }>(
            `/tenants/orgs/${id}/audit`
          );
          setAudit(a.items);
        }
      } catch (e) {
        setTabError(e instanceof Error ? e.message : "Failed to load tab");
      }
    },
    [id]
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
                ? "border border-b-0 border-slate-800 bg-slate-900 text-white"
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
          <EmptyState text="Usage counters appear here once telemetry is enabled (TELEMETRY_ENABLED=true)." />
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
        <div className="mt-4">
          <EmptyState text="Billing data (invoices, MRR, plans) arrives in Phase 5." />
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
                    <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs">
                      {a.action}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">
                    {JSON.stringify(a.new_values ?? a.old_values ?? {})}
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
        <div className="mt-4">
          <EmptyState text="Impersonation and conversation peek arrive in Phase 8." />
        </div>
      )}
    </div>
  );
}
