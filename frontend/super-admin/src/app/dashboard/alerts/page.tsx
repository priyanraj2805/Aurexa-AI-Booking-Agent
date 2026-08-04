"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";

type AlertRow = {
  id: string;
  org_id: string | null;
  org_name: string | null;
  component: string;
  severity: string;
  code: string;
  message: string;
  occurrence_count: number;
  created_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
};

const SEVERITIES = ["info", "warning", "critical"];

const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString() : "—";

export default function AlertsPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const [items, setItems] = useState<AlertRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [severity, setSeverity] = useState<string>("");
  const [status, setStatus] = useState<string>("open");
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const q = new URLSearchParams();
      if (severity) q.set("severity", severity);
      if (status) q.set("status", status);
      q.set("page_size", "100");
      const res = await api.get<{ items: AlertRow[]; total: number }>(
        `/alerts?${q.toString()}`
      );
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load alerts");
    } finally {
      setLoading(false);
    }
  }, [severity, status]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (alert: AlertRow, action: "acknowledge" | "resolve") => {
    setBusy(alert.id);
    try {
      await api.post(`/alerts/${alert.id}/${action}`);
      toast("success", `Alert ${action}d`);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : `${action} failed`);
    } finally {
      setBusy(null);
    }
  };

  const severityStyle = (s: string) =>
    s === "critical"
      ? "bg-red-900/60 text-red-300"
      : s === "warning"
        ? "bg-amber-900/60 text-amber-300"
        : "bg-slate-800 text-slate-300";

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold">Alerts</h1>
        <p className="mt-1 text-sm text-slate-400">
          Platform-wide system alerts. Acknowledge to take ownership; resolve
          when the issue is fixed.
        </p>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-3 text-sm">
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
        <label className="flex items-center gap-2 text-slate-300">
          <span className="text-slate-400">Status</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 outline-none focus:border-indigo-500"
          >
            <option value="">all</option>
            <option value="open">open</option>
            <option value="resolved">resolved</option>
          </select>
        </label>
        <button
          onClick={() => void load()}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800"
        >
          Refresh
        </button>
        <span className="text-slate-500">{total} alerts</span>
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
              <th className="px-4 py-3">Component</th>
              <th className="px-4 py-3">Message</th>
              <th className="px-4 py-3">Org</th>
              <th className="px-4 py-3">Created</th>
              <th className="px-4 py-3">State</th>
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
              items.map((a) => (
                <tr key={a.id} className={a.resolved_at ? "opacity-60" : ""}>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${severityStyle(a.severity)}`}
                    >
                      {a.severity.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">
                    {a.component}
                  </td>
                  <td className="max-w-xs px-4 py-3">
                    <div className="text-slate-200">{a.message}</div>
                    <div className="text-xs text-slate-500">
                      {a.code}
                      {a.occurrence_count > 1
                        ? ` · ×${a.occurrence_count}`
                        : ""}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {a.org_name ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-400">
                    {fmtTime(a.created_at)}
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {a.resolved_at ? (
                      <span className="text-green-300">resolved {fmtTime(a.resolved_at)}</span>
                    ) : a.acknowledged_at ? (
                      <span className="text-amber-300">acknowledged {fmtTime(a.acknowledged_at)}</span>
                    ) : (
                      <span className="text-red-300">open</span>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      {can("alerts.manage") && !a.resolved_at && (
                        <>
                          {!a.acknowledged_at && (
                            <button
                              onClick={() => void act(a, "acknowledge")}
                              disabled={busy === a.id}
                              className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                            >
                              Acknowledge
                            </button>
                          )}
                          <button
                            onClick={() => void act(a, "resolve")}
                            disabled={busy === a.id}
                            className="rounded border border-slate-700 px-2 py-1 text-xs text-green-400 hover:bg-green-950 disabled:opacity-50"
                          >
                            Resolve
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                  No alerts match the current filters
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
