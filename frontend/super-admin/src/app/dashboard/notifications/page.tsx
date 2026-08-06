"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal, ConfirmDialog } from "@/components/modal";

type BroadcastRow = {
  id: string;
  title: string;
  body: string;
  audience: "all_orgs" | "selected_orgs" | "staff";
  org_ids: string[];
  status: "draft" | "reviewed" | "sent" | "cancelled";
  reviewed_at: string | null;
  sent_at: string | null;
  created_at: string | null;
  updated_at: string | null;
};

type OrgRow = { id: string; name: string; business_name: string | null };

const STATUSES = ["all", "draft", "reviewed", "sent", "cancelled"] as const;
const AUDIENCE_LABEL: Record<BroadcastRow["audience"], string> = {
  all_orgs: "All orgs",
  selected_orgs: "Selected orgs",
  staff: "Staff",
};

const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleString() : "—";

export default function NotificationsPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const [items, setItems] = useState<BroadcastRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("all");

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<BroadcastRow | null>(null);
  const [form, setForm] = useState({
    title: "",
    body: "",
    audience: "all_orgs" as BroadcastRow["audience"],
    org_ids: [] as string[],
  });
  const [saving, setSaving] = useState(false);

  const [orgSearch, setOrgSearch] = useState("");
  const [orgOptions, setOrgOptions] = useState<OrgRow[]>([]);

  const [reviewing, setReviewing] = useState<BroadcastRow | null>(null);
  const [reviewingBusy, setReviewingBusy] = useState(false);
  const [sending, setSending] = useState<BroadcastRow | null>(null);
  const [sendingBusy, setSendingBusy] = useState(false);
  const [cancelling, setCancelling] = useState<BroadcastRow | null>(null);
  const [cancellingBusy, setCancellingBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(
        (
          await api.get<{ items: BroadcastRow[] }>(
            `/notifications?page_size=100${
              statusFilter !== "all" ? `&status=${statusFilter}` : ""
            }`
          )
        ).items
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load notifications");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({ title: "", body: "", audience: "all_orgs", org_ids: [] });
    setCreateOpen(true);
  };

  const openEdit = (b: BroadcastRow) => {
    setEditing(b);
    setForm({
      title: b.title,
      body: b.body,
      audience: b.audience,
      org_ids: b.org_ids ?? [],
    });
    setCreateOpen(true);
  };

  const searchOrgs = async () => {
    try {
      const res = await api.get<{ items: OrgRow[] }>(
        `/tenants/orgs?search=${encodeURIComponent(orgSearch)}`
      );
      setOrgOptions(res.items);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Organization search failed");
    }
  };

  const toggleOrg = (id: string) => {
    setForm((f) => ({
      ...f,
      org_ids: f.org_ids.includes(id)
        ? f.org_ids.filter((x) => x !== id)
        : [...f.org_ids, id],
    }));
  };

  const saveBroadcast = async () => {
    setSaving(true);
    try {
      const body = {
        title: form.title.trim(),
        body: form.body.trim(),
        audience: form.audience,
        org_ids: form.org_ids,
      };
      if (editing) {
        await api.patch(`/notifications/${editing.id}`, body);
        toast("success", "Broadcast updated");
      } else {
        await api.post<{ broadcast_id: string }>("/notifications", body);
        toast("success", "Broadcast draft created");
      }
      setCreateOpen(false);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const doReview = async () => {
    if (!reviewing) return;
    setReviewingBusy(true);
    try {
      await api.post(`/notifications/${reviewing.id}/review`);
      toast("success", `"${reviewing.title}" marked reviewed`);
      setReviewing(null);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Review failed");
    } finally {
      setReviewingBusy(false);
    }
  };

  const doSend = async () => {
    if (!sending) return;
    setSendingBusy(true);
    try {
      const res = await api.post<{ recipient_count: number }>(
        `/notifications/${sending.id}/send`
      );
      toast("success", `"${sending.title}" sent to ${res.recipient_count} recipient(s)`);
      setSending(null);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Send failed");
    } finally {
      setSendingBusy(false);
    }
  };

  const doCancel = async () => {
    if (!cancelling) return;
    setCancellingBusy(true);
    try {
      await api.post(`/notifications/${cancelling.id}/cancel`);
      toast("success", `"${cancelling.title}" cancelled`);
      setCancelling(null);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setCancellingBusy(false);
    }
  };

  const statusBadge = (s: string) =>
    s === "sent"
      ? "bg-green-900/60 text-green-300"
      : s === "reviewed"
        ? "bg-sky-900/60 text-sky-300"
        : s === "cancelled"
          ? "bg-slate-800 text-slate-400"
          : "bg-amber-900/60 text-amber-300";

  const visible = items ?? [];

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Notifications</h1>
          <p className="mt-1 text-sm text-slate-400">
            Broadcast notifications with a draft → review → send workflow. Every
            transition is audit-logged.
          </p>
        </div>
        {can("notifications.manage") && (
          <button
            onClick={openCreate}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            New broadcast
          </button>
        )}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200 outline-none focus:border-indigo-500"
        >
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s === "all" ? "All statuses" : s}
            </option>
          ))}
        </select>
        <span className="text-sm text-slate-400">{visible.length} broadcasts</span>
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-6 overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">Broadcast</th>
              <th className="px-4 py-3">Audience</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Reviewed</th>
              <th className="px-4 py-3">Sent</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {loading &&
              [0, 1, 2].map((i) => (
                <tr key={i}>
                  <td colSpan={6} className="px-4 py-3 text-slate-500">
                    Loading…
                  </td>
                </tr>
              ))}
            {!loading &&
              visible.map((b) => (
                <tr key={b.id} className={b.status === "cancelled" ? "opacity-50" : ""}>
                  <td className="max-w-xs px-4 py-3">
                    <div className="font-medium text-slate-100">{b.title}</div>
                    <div className="line-clamp-2 text-xs text-slate-500">{b.body}</div>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-300">
                    {AUDIENCE_LABEL[b.audience]}
                    {b.audience === "selected_orgs" && (
                      <div className="text-slate-500">
                        {(b.org_ids ?? []).length} org(s)
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${statusBadge(b.status)}`}
                    >
                      {b.status.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {fmtDate(b.reviewed_at)}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {fmtDate(b.sent_at)}
                  </td>
                  <td className="px-4 py-3">
                    {can("notifications.manage") && (
                      <div className="flex flex-wrap gap-2">
                        {b.status === "draft" && (
                          <>
                            <button
                              onClick={() => openEdit(b)}
                              className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => setReviewing(b)}
                              className="rounded border border-slate-700 px-2 py-1 text-xs text-sky-300 hover:bg-slate-800"
                            >
                              Review
                            </button>
                          </>
                        )}
                        {b.status === "reviewed" && (
                          <button
                            onClick={() => setSending(b)}
                            className="rounded border border-green-800 px-2 py-1 text-xs text-green-300 hover:bg-green-950"
                          >
                            Send
                          </button>
                        )}
                        {(b.status === "draft" || b.status === "reviewed") && (
                          <button
                            onClick={() => setCancelling(b)}
                            className="rounded border border-slate-700 px-2 py-1 text-xs text-red-400 hover:bg-red-950"
                          >
                            Cancel
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            {!loading && visible.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  No broadcasts match the current filter
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {createOpen && (
        <Modal
          title={editing ? `Edit ${editing.title}` : "New broadcast"}
          onClose={() => setCreateOpen(false)}
        >
          <div className="space-y-3 text-sm">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Title *
              </label>
              <input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="Scheduled maintenance tonight"
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Message *
              </label>
              <textarea
                value={form.body}
                onChange={(e) => setForm({ ...form, body: e.target.value })}
                rows={4}
                placeholder="We'll be performing maintenance from 23:00–23:30 UTC…"
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Audience
              </label>
              <select
                value={form.audience}
                onChange={(e) =>
                  setForm({ ...form, audience: e.target.value as BroadcastRow["audience"] })
                }
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              >
                <option value="all_orgs">All organizations</option>
                <option value="selected_orgs">Selected organizations</option>
                <option value="staff">Platform staff</option>
              </select>
            </div>
            {form.audience === "selected_orgs" && (
              <div className="rounded-md border border-slate-800 p-3">
                <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                  Recipients ({form.org_ids.length} selected)
                </p>
                <div className="flex gap-2">
                  <input
                    value={orgSearch}
                    onChange={(e) => setOrgSearch(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && void searchOrgs()}
                    placeholder="Search orgs…"
                    className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                  />
                  <button
                    onClick={searchOrgs}
                    className="rounded-md border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800"
                  >
                    Search
                  </button>
                </div>
                {orgOptions.length > 0 && (
                  <div className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                    {orgOptions.map((o) => (
                      <button
                        key={o.id}
                        onClick={() => toggleOrg(o.id)}
                        className={`block w-full rounded-md border px-3 py-1.5 text-left ${
                          form.org_ids.includes(o.id)
                            ? "border-indigo-500 bg-indigo-950/50"
                            : "border-slate-800 hover:bg-slate-800"
                        }`}
                      >
                        {o.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setCreateOpen(false)}
              disabled={saving}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={saveBroadcast}
              disabled={
                saving ||
                form.title.trim().length < 3 ||
                form.body.trim().length < 3 ||
                (form.audience === "selected_orgs" && form.org_ids.length === 0)
              }
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? "Saving…" : editing ? "Save changes" : "Create draft"}
            </button>
          </div>
        </Modal>
      )}

      {reviewing && (
        <ConfirmDialog
          title="Review broadcast"
          message={
            <span>
              Mark <strong>{reviewing.title}</strong> as reviewed? It can then be
              sent to the audience.
            </span>
          }
          confirmLabel="Mark reviewed"
          busy={reviewingBusy}
          onConfirm={doReview}
          onCancel={() => setReviewing(null)}
        />
      )}

      {sending && (
        <ConfirmDialog
          title="Send broadcast"
          message={
            <span>
              Send <strong>{sending.title}</strong> to{" "}
              {sending.audience === "all_orgs"
                ? "all organizations"
                : sending.audience === "staff"
                  ? "platform staff"
                  : `${(sending.org_ids ?? []).length} selected organizations`}
              ? This cannot be undone.
            </span>
          }
          confirmLabel="Send"
          busy={sendingBusy}
          onConfirm={doSend}
          onCancel={() => setSending(null)}
        />
      )}

      {cancelling && (
        <ConfirmDialog
          title="Cancel broadcast"
          message={
            <span>
              Cancel <strong>{cancelling.title}</strong>? A cancelled broadcast
              cannot be sent or edited.
            </span>
          }
          confirmLabel="Cancel broadcast"
          danger
          busy={cancellingBusy}
          onConfirm={doCancel}
          onCancel={() => setCancelling(null)}
        />
      )}
    </div>
  );
}
