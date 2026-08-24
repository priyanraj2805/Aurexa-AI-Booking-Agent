"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal } from "@/components/modal";
import { Pagination } from "@/components/pagination";
import { SearchInput, CheckboxFilter, DateFilter, SortableTh, isDateRangeValid, toApiDate, toApiDateTo } from "@/components/filter-bar";

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

const SEVERITY_OPTIONS = SEVERITIES.map((s) => ({ value: s, label: s }));
const STATUS_OPTIONS = STATUSES.map((s) => ({ value: s, label: s }));
const COMPONENT_OPTIONS = [
  { value: "database", label: "Database" },
  { value: "api", label: "API" },
  { value: "queue", label: "Queue" },
  { value: "worker", label: "Worker" },
  { value: "scheduler", label: "Scheduler" },
  { value: "frontend", label: "Frontend" },
  { value: "auth", label: "Auth" },
  { value: "billing", label: "Billing" },
  { value: "notifications", label: "Notifications" },
  { value: "unknown", label: "Unknown" },
];

const SORT_DEFAULTS: Record<string, "asc" | "desc"> = {
  created_at: "desc",
  updated_at: "desc",
  resolved_at: "desc",
};

const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString() : "—";

export default function IncidentsPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const [items, setItems] = useState<IncidentRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [statusFilters, setStatusFilters] = useState<string[]>([]);
  const [severityFilters, setSeverityFilters] = useState<string[]>([]);
  const [componentFilters, setComponentFilters] = useState<string[]>([]);
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
    if (!isDateRangeValid(fromDate, toDate)) {
      setItems([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    try {
      const q = new URLSearchParams();
      for (const s of statusFilters) q.append("status", s);
      for (const s of severityFilters) q.append("severity", s);
      for (const c of componentFilters) q.append("component", c);
      if (search) q.append("search", search);
      if (fromDate) q.append("from_date", toApiDate(fromDate) || fromDate);
      if (toDate) q.append("to_date", toApiDateTo(toDate) || toDate);
      q.append("sort_by", sortBy);
      q.append("sort_order", sortOrder);
      q.append("page", String(page));
      q.append("page_size", "10");
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
  }, [statusFilters, severityFilters, componentFilters, search, fromDate, toDate, sortBy, sortOrder, page]);

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

  const fmtTime = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString() : "—";

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

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          placeholder="Search title, message…"
          className="w-64"
        />
        <CheckboxFilter
          label="Status"
          options={[
            { value: "open", label: "Open" },
            { value: "investigating", label: "Investigating" },
            { value: "resolved", label: "Resolved" },
          ]}
          selected={statusFilters}
          onChange={(v) => {
            setStatusFilters(v);
            setPage(1);
          }}
        />
        <CheckboxFilter
          label="Severity"
          options={[
            { value: "critical", label: "Critical" },
            { value: "major", label: "Major" },
            { value: "minor", label: "Minor" },
          ]}
          selected={severityFilters}
          onChange={(v) => {
            setSeverityFilters(v);
            setPage(1);
          }}
        />
        <CheckboxFilter
          label="Component"
          options={[
            { value: "database", label: "Database" },
            { value: "api", label: "API" },
            { value: "queue", label: "Queue" },
            { value: "worker", label: "Worker" },
            { value: "scheduler", label: "Scheduler" },
            { value: "frontend", label: "Frontend" },
            { value: "auth", label: "Auth" },
            { value: "billing", label: "Billing" },
            { value: "notifications", label: "Notifications" },
            { value: "unknown", label: "Unknown" },
          ]}
          selected={componentFilters}
          onChange={(v) => {
            setComponentFilters(v);
            setPage(1);
          }}
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

      <div className="mt-4 overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">Severity</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Title</th>
              <th className="px-4 py-3">Component</th>
              <SortableTh
                column="created_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
              >
                Created
              </SortableTh>
              <SortableTh
                column="resolved_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
              >
                Resolved
              </SortableTh>
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
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        i.severity === "critical"
                          ? "bg-red-900/60 text-red-300"
                          : i.severity === "major"
                            ? "bg-amber-900/60 text-amber-300"
                            : "bg-slate-800 text-slate-300"
                      }`}
                    >
                      {i.severity.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        i.status === "resolved"
                          ? "bg-green-900/60 text-green-300"
                          : i.status === "investigating"
                            ? "bg-blue-900/60 text-blue-300"
                            : "bg-red-900/60 text-red-300"
                      }`}
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

      <Pagination page={page} pageSize={10} total={total} onPageChange={setPage} />

      {createOpen && (
        <Modal title="New incident" onClose={() => setCreateOpen(false)}
          confirmOnClose={!!(form.title || form.message || form.component)}
          confirmMessage="You have unsaved incident details. Discard them?"
        >
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
                  {["critical", "major", "minor"].map((s) => (
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