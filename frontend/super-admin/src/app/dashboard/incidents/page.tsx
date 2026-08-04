"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal } from "@/components/modal";

type IncidentRow = {
  id: string;
  title: string;
  severity: string;
  status: string;
  component: string | null;
  message: string | null;
  created_by_id: string | null;
  resolved_by_id: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
};

const SEVERITIES = ["critical", "major", "minor"];
const STATUSES = ["open", "investigating", "resolved"];

const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString() : "—";

export default function IncidentsPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const [items, setItems] = useState<IncidentRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [status, setStatus] = useState<string>("");
  const [severity, setSeverity] = useState<string>("");
  const [busy, setBusy] = useState<string | null>(null);

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({
    title: "",
    severity: "major",
    component: "",
    message: "",
  });
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const q = new URLSearchParams();
      if (status) q.set("status", status);
      if (severity) q.set("severity", severity);
      q.set("page_size", "100");
      const res = await api.get<{ items: IncidentRow[]; total: number }>(
        `/incidents?${q.toString()}`
      );
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load incidents");
    } finally {
      setLoading(false);
    }
  }, [status, severity]);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setForm({ title: "", severity: "major", component: "", message: "" });
    setCreateOpen(true);
  };

  const createIncident = async () => {
    setSaving(true);
    try {
      await api.post("/incidents", {
        title: form.title.trim(),
        severity: form.severity,
        component: form.component.trim() || "unknown",
        message: form.message.trim(),
      });
      toast("success", "Incident created");
      setCreateOpen(false);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Create failed");
    } finally {
      setSaving(false);
    }
  };

  const updateStatus = async (incident: IncidentRow, nextStatus: string) => {
    setBusy(incident.id);
    try {
      await api.patch(`/incidents/${incident.id}`, { status: nextStatus });
      toast(
        "success",
        nextStatus === "resolved"
          ? `${incident.title} resolved`
          : `${incident.title} → ${nextStatus}`
      );
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusy(null);
    }
  };

  const severityStyle = (s: string) =>
    s === "critical"
      ? "bg-red-900/60 text-red-300"
      : s === "major"
        ? "bg-amber-900/60 text-amber-300"
        : "bg-slate-800 text-slate-300";

  const statusStyle = (s: string) =>
    s === "resolved"
      ? "bg-green-900/60 text-green-300"
      : s === "investigating"
        ? "bg-blue-900/60 text-blue-300"
        : "bg-red-900/60 text-red-300";

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Incidents</h1>
          <p className="mt-1 text-sm text-slate-400">
            Tracked operational events with an open → investigating → resolved
            lifecycle.
          </p>
        </div>
        {can("incidents.manage") && (
          <button
            onClick={openCreate}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            New incident
          </button>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2 text-slate-300">
          <span className="text-slate-400">Status</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 outline-none focus:border-indigo-500"
          >
            <option value="">all</option>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-slate-300">
          <span className="text-slate-400">Severity</span>
          <select
            value={severity}
            onChange={(e) => setSeverity(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 outline-none focus:border-indigo-500"
          >
            <option value="">all</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <button
          onClick={() => void load()}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800"
        >
          Refresh
        </button>
        <span className="text-slate-500">{total} incidents</span>
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-4 overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">Severity</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Title</th>
              <th className="px-4 py-3">Component</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3">Resolved</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {loading && (
              <tr>
                <td colSpan={7} className="px-4 py-3 text-slate-500">
                  Loading…
                </td>
              </tr>
            )}
            {!loading &&
              items.map((i) => (
                <tr key={i.id} className={i.status === "resolved" ? "opacity-60" : ""}>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${severityStyle(i.severity)}`}
                    >
                      {i.severity.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${statusStyle(i.status)}`}
                    >
                      {i.status.toUpperCase()}
                    </span>
                  </td>
                  <td className="max-w-sm px-4 py-3">
                    <div className="font-medium text-slate-100">{i.title}</div>
                    {i.message && (
                      <div className="mt-0.5 text-xs text-slate-500">{i.message}</div>
                    )}
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-400">
                    {i.component ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">
                    {fmtTime(i.created_at)}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">
                    {fmtTime(i.resolved_at)}
                  </td>
                  <td className="px-4 py-3">
                    {can("incidents.manage") && i.status !== "resolved" && (
                      <div className="flex gap-2">
                        {i.status === "open" && (
                          <button
                            onClick={() => void updateStatus(i, "investigating")}
                            disabled={busy === i.id}
                            className="rounded border border-slate-700 px-2 py-1 text-xs text-blue-300 hover:bg-blue-950 disabled:opacity-50"
                          >
                            Investigate
                          </button>
                        )}
                        {i.status !== "open" && (
                          <button
                            onClick={() => void updateStatus(i, "open")}
                            disabled={busy === i.id}
                            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                          >
                            Reopen
                          </button>
                        )}
                        <button
                          onClick={() => void updateStatus(i, "resolved")}
                          disabled={busy === i.id}
                          className="rounded border border-slate-700 px-2 py-1 text-xs text-green-400 hover:bg-green-950 disabled:opacity-50"
                        >
                          Resolve
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                  No incidents match the current filters
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {createOpen && (
        <Modal title="New incident" onClose={() => setCreateOpen(false)}>
          <div className="space-y-3 text-sm">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Title *
              </label>
              <input
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                placeholder="LLM API latency spike"
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                  Severity
                </label>
                <select
                  value={form.severity}
                  onChange={(e) => setForm({ ...form, severity: e.target.value })}
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                >
                  {SEVERITIES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                  Component
                </label>
                <input
                  value={form.component}
                  onChange={(e) => setForm({ ...form, component: e.target.value })}
                  placeholder="database"
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Message
              </label>
              <textarea
                value={form.message}
                onChange={(e) => setForm({ ...form, message: e.target.value })}
                rows={3}
                placeholder="What happened, what is being done…"
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
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
              onClick={createIncident}
              disabled={saving || form.title.trim().length < 2}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? "Creating…" : "Create incident"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
