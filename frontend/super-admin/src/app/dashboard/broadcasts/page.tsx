"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal, ConfirmDialog } from "@/components/modal";
import { Pagination } from "@/components/pagination";
import { SearchInput, DateFilter, SortableTh, isDateRangeValid, toApiDate, toApiDateTo } from "@/components/filter-bar";
import { OrgCombobox } from "@/components/org-combobox";

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

const SORT_DEFAULTS: Record<string, "asc" | "desc"> = {
  created_at: "desc",
  updated_at: "desc",
  sent_at: "desc",
  reviewed_at: "desc",
};
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
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  const handleSort = (column: string, order: "asc" | "desc") => {
    setSortBy(column);
    setSortOrder(order);
    setPage(1);
  };

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<BroadcastRow | null>(null);
  const [form, setForm] = useState({
    title: "",
    body: "",
    audience: "all_orgs" as BroadcastRow["audience"],
    org_ids: [] as string[],
  });
  const [saving, setSaving] = useState(false);

  const [reviewing, setReviewing] = useState<BroadcastRow | null>(null);
  const [reviewingBusy, setReviewingBusy] = useState(false);
  const [sending, setSending] = useState<BroadcastRow | null>(null);
  const [sendingBusy, setSendingBusy] = useState(false);
  const [cancelling, setCancelling] = useState<BroadcastRow | null>(null);
  const [cancellingBusy, setCancellingBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    if (!isDateRangeValid(fromDate, toDate)) {
      setItems([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: "10",
        sort_by: sortBy,
        sort_order: sortOrder,
      });
      if (search) params.append("search", search);
      if (fromDate) params.append("from_date", toApiDate(fromDate) || fromDate);
      if (toDate) params.append("to_date", toApiDateTo(toDate) || toDate);
      const res = await api.get<{ items: BroadcastRow[]; total: number }>(
        `/notifications?${params.toString()}`
      );
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load broadcasts");
    } finally {
      setLoading(false);
    }
  }, [search, fromDate, toDate, sortBy, sortOrder, page]);

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

  const openDuplicate = (b: BroadcastRow) => {
    setEditing(null);
    setForm({
      title: `${b.title} (copy)`,
      body: b.body,
      audience: b.audience,
      org_ids: b.org_ids ?? [],
    });
    setCreateOpen(true);
  };

  const toggleOrg = (id: string) => {
    setForm((f) => ({
      ...f,
      org_ids: f.org_ids.includes(id) ? f.org_ids.filter((x) => x !== id) : [...f.org_ids, id],
    }));
  };

  const saveBroadcast = async () => {
    if (!form.title.trim() || !form.body.trim()) {
      toast("error", "Title and body are required");
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await api.patch(`/notifications/${editing.id}`, {
          title: form.title.trim(),
          body: form.body.trim(),
          audience: form.audience,
          org_ids: form.org_ids,
        });
        toast("success", "Broadcast updated");
      } else {
        await api.post("/notifications", {
          title: form.title.trim(),
          body: form.body.trim(),
          audience: form.audience,
          org_ids: form.org_ids,
        });
        toast("success", "Broadcast created");
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
      toast("success", "Broadcast reviewed");
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
      await api.post(`/notifications/${sending.id}/send`);
      toast("success", "Broadcast sent");
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
      toast("success", "Broadcast cancelled");
      setCancelling(null);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setCancellingBusy(false);
    }
  };

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">Broadcasts</h1>
          <p className="mt-1 text-sm text-slate-400">
            Broadcast announcements with a draft → review → send workflow. Every
            transition is audit-logged.
          </p>
        </div>
        {can("notifications.manage") && (
          <button
            onClick={openCreate}
            className="min-h-11 shrink-0 rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            New broadcast
          </button>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          placeholder="Search title or body…"
          className="w-full sm:w-64"
        />
        <DateFilter
          label="Created"
          fromDate={fromDate}
          toDate={toDate}
          onFromChange={setFromDate}
          onToChange={setToDate}
        />
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
              <SortableTh
                column="reviewed_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.reviewed_at}
              >
                Reviewed
              </SortableTh>
              <SortableTh
                column="sent_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.sent_at}
              >
                Sent
              </SortableTh>
              <SortableTh
                column="created_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.created_at}
              >
                Created
              </SortableTh>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {loading &&
              [0, 1, 2].map((i) => (
                <tr key={i}>
                  <td colSpan={7} className="px-4 py-3 text-slate-500">
                    Loading…
                  </td>
                </tr>
              ))}
            {!loading &&
              (items ?? []).map((b) => (
                <tr key={b.id} className={b.status === "cancelled" ? "opacity-50" : ""}>
                  <td className="max-w-xs px-4 py-3">
                    <div className="font-medium text-slate-100">{b.title}</div>
                    <div className="text-xs text-slate-500 truncate">{b.body}</div>
                  </td>
                  <td className="px-4 py-3">{AUDIENCE_LABEL[b.audience]}</td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        b.status === "draft"
                          ? "bg-slate-800 text-slate-400"
                          : b.status === "reviewed"
                          ? "bg-amber-950/60 text-amber-300"
                          : b.status === "sent"
                          ? "bg-green-950/60 text-green-300"
                          : "bg-red-950/60 text-red-300"
                      }`}
                    >
                      {b.status.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">{fmtDate(b.reviewed_at)}</td>
                  <td className="px-4 py-3 text-xs text-slate-400">{fmtDate(b.sent_at)}</td>
                  <td className="px-4 py-3 text-xs text-slate-400">{fmtDate(b.created_at)}</td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      {can("notifications.manage") && (
                        <>
                          {b.status === "draft" && (
                            <>
                              <button
                                onClick={() => setReviewing(b)}
                                disabled={reviewingBusy}
                                className="min-h-10 rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                              >
                                Review
                              </button>
                              <button
                                onClick={() => setEditing(b)}
                                className="min-h-10 rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
                              >
                                Edit
                              </button>
                              <button
                                onClick={() => openDuplicate(b)}
                                className="min-h-10 rounded border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
                              >
                                Duplicate
                              </button>
                            </>
                          )}
                          {b.status === "reviewed" && (
                            <button
                              onClick={() => setSending(b)}
                              disabled={sendingBusy}
                              className="min-h-10 rounded bg-indigo-600 px-3 py-2 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                            >
                              Send
                            </button>
                          )}
                          {["draft", "reviewed"].includes(b.status) && (
                            <button
                              onClick={() => setCancelling(b)}
                              disabled={cancellingBusy}
                              className="min-h-10 rounded border border-red-600 bg-red-950/50 px-3 py-2 text-xs text-red-300 hover:bg-red-950 disabled:opacity-50"
                            >
                              Cancel
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            {!loading && (!items || items.length === 0) && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                  No broadcasts found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {items && (
        <Pagination page={page} pageSize={10} total={total} onPageChange={setPage} />
      )}

      {createOpen && (
        <Modal title={editing ? `Edit ${editing.title}` : "New broadcast"} onClose={() => setCreateOpen(false)}
          confirmOnClose={!!(form.title || form.body)}
          confirmMessage="You have unsaved broadcast changes. Discard them?"
        >
          <div className="space-y-3 text-sm">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">Title *</label>
              <input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">Body *</label>
              <textarea
                value={form.body}
                onChange={(e) => setForm({ ...form, body: e.target.value })}
                rows={4}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">Audience</label>
              <select
                value={form.audience}
                onChange={(e) => setForm({ ...form, audience: e.target.value as BroadcastRow["audience"] })}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              >
                <option value="all_orgs">All orgs</option>
                <option value="selected_orgs">Selected orgs</option>
                <option value="staff">Staff</option>
              </select>
            </div>
            {form.audience === "selected_orgs" && (
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">Organizations</label>
                <OrgCombobox
                  multiple
                  value={form.org_ids}
                  onSelect={(o) => toggleOrg(o.id)}
                  onRemove={(id) => toggleOrg(id)}
                  placeholder="Type to search — e.g. Acme…"
                />
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
              disabled={saving || !form.title.trim() || !form.body.trim()}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? "Saving…" : editing ? "Save changes" : "Create broadcast"}
            </button>
          </div>
        </Modal>
      )}

      {reviewing && (
        <ConfirmDialog
          title="Review broadcast"
          message={<>Review <strong>{reviewing.title}</strong>? This moves it to "Reviewed" status.</>}
          confirmLabel="Review"
          busy={reviewingBusy}
          onConfirm={doReview}
          onCancel={() => setReviewing(null)}
        />
      )}

      {sending && (
        <ConfirmDialog
          title="Send broadcast"
          message={<>Send <strong>{sending.title}</strong> to all recipients?</>}
          confirmLabel="Send"
          busy={sendingBusy}
          onConfirm={doSend}
          onCancel={() => setSending(null)}
        />
      )}

      {cancelling && (
        <ConfirmDialog
          title="Cancel broadcast"
          message={<>Cancel <strong>{cancelling.title}</strong>? This action cannot be undone.</>}
          confirmLabel="Cancel"
          danger
          busy={cancellingBusy}
          onConfirm={doCancel}
          onCancel={() => setCancelling(null)}
        />
      )}
    </div>
  );
}