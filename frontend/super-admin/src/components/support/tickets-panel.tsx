"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal } from "@/components/modal";

type Ticket = {
  id: string;
  org_name: string | null;
  org_id: string;
  reporter_email: string;
  reporter_name: string | null;
  subject: string;
  detail: string;
  priority: string;
  status: string;
  reply: string | null;
  replied_by_email: string | null;
  replied_at: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
};

const fmtDate = (s: string | null) => (s ? new Date(s).toLocaleString() : "—");

const statusBadge = (s: string) =>
  s === "open"
    ? "bg-amber-950/60 text-amber-300"
    : s === "in_progress"
      ? "bg-sky-950/60 text-sky-300"
      : s === "resolved"
        ? "bg-green-950/60 text-green-300"
        : "bg-slate-800 text-slate-400";

const priorityStyle = (p: string) =>
  p === "urgent"
    ? "text-red-400"
    : p === "high"
      ? "text-orange-300"
      : p === "low"
        ? "text-slate-400"
        : "text-slate-300";

export default function TicketsPanel() {
  const { can } = useAuth();
  const { toast } = useToast();

  const canView = can("support.tickets.view");
  const canManage = can("support.tickets.manage");

  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [priorityFilter, setPriorityFilter] = useState("");
  const [managed, setManaged] = useState<Ticket | null>(null);
  const [newStatus, setNewStatus] = useState("");
  const [replyText, setReplyText] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    if (!canView) return;
    const params = new URLSearchParams();
    if (statusFilter) params.set("status", statusFilter);
    if (priorityFilter) params.set("priority", priorityFilter);
    try {
      const res = await api.get<{ items: Ticket[] }>(
        `/support/queue${params.toString() ? `?${params.toString()}` : ""}`
      );
      setTickets(res.items);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to load tickets");
      setTickets([]);
    }
  }, [canView, statusFilter, priorityFilter, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  const openManage = (t: Ticket) => {
    setManaged(t);
    setNewStatus(t.status);
    setReplyText(t.reply ?? "");
  };

  const saveManage = async () => {
    if (!managed) return;
    setSaving(true);
    try {
      await api.post(`/support/tickets/${managed.id}/manage`, {
        status: newStatus,
        reply: replyText.trim() || null,
      });
      toast("success", "Ticket updated");
      setManaged(null);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Update failed");
    } finally {
      setSaving(false);
    }
  };

  const counts = (tickets ?? []).reduce(
    (acc, t) => ({ ...acc, [t.status]: (acc[t.status] ?? 0) + 1 }),
    {} as Record<string, number>
  );

  if (!canView) {
    return (
      <div className="rounded-lg border border-slate-800 p-6 text-slate-400">
        You don't have permission to view the support ticket queue.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-slate-400">
        Requests filed by org admins. Triage the queue, change status and
        reply — the org admin is notified by email.
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <div className="flex gap-1.5 text-xs">
          {(["", "open", "in_progress", "resolved"] as const).map((s) => (
            <button
              key={s || "all"}
              onClick={() => setStatusFilter(s)}
              className={`rounded-full border px-3 py-1 ${
                statusFilter === s
                  ? "border-indigo-600 bg-indigo-600 text-white"
                  : "border-slate-700 text-slate-300 hover:bg-slate-800"
              }`}
            >
              {s === "" ? "All" : s.replace("_", " ")}{" "}
              {s && counts[s] ? `(${counts[s]})` : ""}
            </button>
          ))}
        </div>
        <div className="ml-auto">
          <select
            value={priorityFilter}
            onChange={(e) => setPriorityFilter(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-300 outline-none"
          >
            <option value="">All priorities</option>
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="normal">Normal</option>
            <option value="low">Low</option>
          </select>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Priority</th>
              <th className="px-4 py-3">Org</th>
              <th className="px-4 py-3">Reporter</th>
              <th className="px-4 py-3">Subject</th>
              <th className="px-4 py-3">Filed</th>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {(tickets ?? []).map((t) => (
              <tr key={t.id} className="hover:bg-slate-800/40">
                <td className="px-4 py-3">
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${statusBadge(t.status)}`}
                  >
                    {t.status.toUpperCase().replace("_", " ")}
                  </span>
                </td>
                <td className="px-4 py-3">
                  <span className={`font-medium ${priorityStyle(t.priority)}`}>
                    {t.priority}
                  </span>
                </td>
                <td className="px-4 py-3">{t.org_name ?? "—"}</td>
                <td className="px-4 py-3">
                  <p className="text-slate-200">{t.reporter_name || "—"}</p>
                  <p className="font-mono text-xs text-slate-400">
                    {t.reporter_email}
                  </p>
                </td>
                <td className="max-w-[260px] truncate px-4 py-3">{t.subject}</td>
                <td className="px-4 py-3 text-xs text-slate-500">
                  {fmtDate(t.created_at)}
                </td>
                <td className="px-4 py-3">
                  {canManage && (
                    <button
                      onClick={() => openManage(t)}
                      className="rounded border border-slate-700 px-2 py-1 text-xs text-indigo-300 hover:bg-slate-800"
                    >
                      Triage
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {tickets && tickets.length === 0 && (
              <tr>
                <td
                  colSpan={7}
                  className="px-4 py-8 text-center text-slate-400"
                >
                  No tickets match the current filters
                </td>
              </tr>
            )}
            {!tickets && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                  Loading…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {managed && (
        <Modal title={`Triage — ${managed.subject}`} onClose={() => !saving && setManaged(null)}>
          <div className="space-y-4 text-sm">
            <div className="rounded-md border border-slate-800 p-3">
              <p className="text-xs text-slate-400">{managed.org_name}</p>
              <p className="mt-1 text-sm text-slate-200">
                {managed.reporter_name} ·{" "}
                <span className="font-mono text-xs">{managed.reporter_email}</span>
              </p>
              <p className="mt-2 whitespace-pre-wrap text-slate-300">
                {managed.detail}
              </p>
            </div>

            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Status
              </label>
              <select
                value={newStatus}
                onChange={(e) => setNewStatus(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              >
                <option value="open">Open</option>
                <option value="in_progress">In progress</option>
                <option value="resolved">Resolved</option>
              </select>
            </div>

            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Reply to {managed.reporter_name || "org admin"}
              </label>
              <textarea
                value={replyText}
                onChange={(e) => setReplyText(e.target.value)}
                rows={4}
                placeholder="Write a reply — it will be emailed to the org admin and shown in their dashboard."
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setManaged(null)}
                disabled={saving}
                className="rounded-md border border-slate-700 px-4 py-2 text-slate-300 hover:bg-slate-800 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={saveManage}
                disabled={saving || (!newStatus && !replyText.trim())}
                className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save & notify"}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}