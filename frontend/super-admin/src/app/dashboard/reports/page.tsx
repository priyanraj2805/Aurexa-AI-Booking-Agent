"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useToast } from "@/components/toast";

const REPORT_TYPES: { name: string; label: string; description: string }[] = [
  { name: "usage", label: "Usage", description: "Bookings & AI tokens per org (monthly)" },
  { name: "revenue", label: "Revenue", description: "Owned by Billing — see Billing → Revenue" },
  { name: "bookings", label: "Bookings", description: "Bookings volume, last 30 days" },
  { name: "conversations", label: "Conversations", description: "Sessions & messages, last 30 days" },
  { name: "ai", label: "AI", description: "AI token usage vs messages, current month" },
  { name: "api", label: "API", description: "Audited actions per org, last 30 days" },
  { name: "storage", label: "Storage", description: "Document chunks & bytes per org" },
  { name: "kb", label: "KB", description: "Knowledge base chunks by source type" },
  { name: "growth", label: "Growth", description: "Orgs & users created, last 12 months" },
  { name: "performance", label: "Performance", description: "Booking throughput & conversion" },
  { name: "widget", label: "Widget", description: "Embedded widget bookings, last 30 days" },
];

type ReportPayload = Record<string, unknown> & {
  report?: string;
  generated_at?: string;
};

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

export default function ReportsPage() {
  const { toast } = useToast();

  const [reports, setReports] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string>("usage");
  const [month, setMonth] = useState<string>(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  });
  const [payload, setPayload] = useState<ReportPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);

  const loadReports = useCallback(async () => {
    try {
      const res = await api.get<{ items: string[] }>("/reports");
      setReports(res.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load report list");
    }
  }, []);

  useEffect(() => {
    void loadReports();
  }, [loadReports]);

  const monthParam = (value: string) => `${value}-01`;

  const loadReport = useCallback(
    async (name: string) => {
      setLoading(true);
      setError(null);
      try {
        const q = name === "usage" ? `?month=${monthParam(month)}` : "";
        const res = await api.get<ReportPayload>(`/reports/${name}${q}`);
        setPayload(res);
      } catch (e) {
        setPayload(null);
        setError(e instanceof Error ? e.message : "Failed to load report");
      } finally {
        setLoading(false);
      }
    },
    [month]
  );

  useEffect(() => {
    void loadReport(selected);
  }, [selected, loadReport]);

  const pickReport = (name: string) => {
    if (name === "revenue") {
      toast("info", "Revenue report lives in Billing → Revenue (with CSV export)");
      return;
    }
    setSelected(name);
  };

  const downloadCsv = async () => {
    setExporting(true);
    try {
      const base = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";
      const q = selected === "usage" ? `?month=${monthParam(month)}` : "";
      const res = await fetch(`${base}/reports/${selected}/csv${q}`, {
        credentials: "include",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = (body as { detail?: unknown }).detail;
        throw new Error(
          typeof detail === "string" ? detail : `CSV export failed (${res.status})`
        );
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${selected}_report.csv`;
      a.click();
      URL.revokeObjectURL(url);
      toast("success", `${selected} report exported as CSV`);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "CSV export failed");
    } finally {
      setExporting(false);
    }
  };

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold">Analytics</h1>
        <p className="mt-1 text-sm text-slate-400">
          Live reports over tenant data. Pick a report type, then view JSON or
          export CSV.
        </p>
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-2">
          {reports === null && (
            <p className="text-sm text-slate-500">Loading report types…</p>
          )}
          {(reports ?? REPORT_TYPES.map((r) => r.name)).map((name) => {
            const meta = REPORT_TYPES.find((r) => r.name === name);
            const active = selected === name;
            return (
              <button
                key={name}
                onClick={() => pickReport(name)}
                className={`block w-full rounded-md border px-3 py-2 text-left ${
                  active
                    ? "border-indigo-500 bg-indigo-950/50"
                    : "border-slate-800 hover:bg-slate-800"
                }`}
              >
                <span className="text-sm font-medium text-slate-200">
                  {meta?.label ?? name}
                </span>
                {meta?.description && (
                  <span className="block text-xs text-slate-500">
                    {meta.description}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        <div className="lg:col-span-2">
          <div className="flex flex-wrap items-center gap-3">
            {selected === "usage" && (
              <label className="flex items-center gap-2 text-sm text-slate-300">
                <span className="text-slate-400">Month</span>
                <input
                  type="month"
                  value={month}
                  onChange={(e) => setMonth(e.target.value)}
                  className="rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm outline-none focus:border-indigo-500"
                />
              </label>
            )}
            <button
              onClick={() => void loadReport(selected)}
              disabled={loading}
              className="rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              {loading ? "Loading…" : "Refresh"}
            </button>
            <button
              onClick={() => void downloadCsv()}
              disabled={exporting || loading || selected === "revenue"}
              className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {exporting ? "Exporting…" : "Download CSV"}
            </button>
            <span className="text-xs text-slate-500">
              {payload?.generated_at
                ? `generated ${new Date(payload.generated_at).toLocaleString()}`
                : ""}
            </span>
          </div>

          <div className="mt-4 rounded-lg border border-slate-800 bg-slate-900/50">
            <div className="border-b border-slate-800 px-4 py-2 text-sm font-medium text-slate-300">
              {REPORT_TYPES.find((r) => r.name === selected)?.label ?? selected}
              {payload && "report" in payload && payload.report ? (
                <span className="ml-2 font-mono text-xs text-slate-500">
                  /reports/{payload.report}
                </span>
              ) : null}
            </div>
            {loading ? (
              <p className="p-4 text-sm text-slate-500">Loading report…</p>
            ) : payload ? (
              <pre className="max-h-[70vh] overflow-auto p-4 text-xs leading-relaxed text-slate-300">
                {pretty(payload)}
              </pre>
            ) : (
              <p className="p-4 text-sm text-slate-500">
                {error ? "Report failed to load." : "Select a report type."}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
