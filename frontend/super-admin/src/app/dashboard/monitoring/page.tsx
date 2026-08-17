"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

type DependencyState = {
  name?: string;
  status: string;
  latency_ms?: number | null;
};

type DepPayload = {
  checks?: Record<string, DependencyState>;
  dependencies?: DependencyState[];
  [key: string]: unknown;
};

type Overview = {
  open_incidents: number;
};

function KpiCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: string | number;
  accent?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${accent ?? "text-slate-100"}`}>
        {value}
      </p>
    </div>
  );
}

export default function MonitoringPage() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [dep, setDep] = useState<DepPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [ov, checks] = await Promise.all([
        api.get<Overview>("/health/overview"),
        api.get<DepPayload>("/health/dependencies").catch(() => null),
      ]);
      setOverview(ov);
      setDep(checks);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load health data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const entries: { name: string; state: string; latency?: number | null }[] = [];
  if (dep) {
    if (dep.checks && typeof dep.checks === "object") {
      for (const [name, state] of Object.entries(dep.checks)) {
        if (state && typeof state === "object") {
          entries.push({
            name,
            state: state.status,
            latency: state.latency_ms,
          });
        }
      }
    } else if (Array.isArray(dep.dependencies)) {
      for (const d of dep.dependencies) {
        entries.push({
          name: d.name ?? "dependency",
          state: d.status,
          latency: d.latency_ms,
        });
      }
    }
  }

  return (
    <div>
      <div>
        <h1 className="text-2xl font-bold">Monitoring</h1>
        <p className="mt-1 text-sm text-slate-400">
          Dependency health from the tenant backend plus platform incident
          counts.
        </p>
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-6 max-w-xs">
        <KpiCard
          label="Open incidents"
          value={overview?.open_incidents ?? "—"}
          accent={
            (overview?.open_incidents ?? 0) > 0 ? "text-amber-300" : "text-green-300"
          }
        />
      </div>

      <div className="mt-6 rounded-lg border border-slate-800 bg-slate-900/50">
        <div className="border-b border-slate-800 px-4 py-2 text-sm font-medium text-slate-300">
          Dependency health
        </div>
        {loading ? (
          <p className="p-4 text-sm text-slate-500">Checking dependencies…</p>
        ) : entries.length === 0 ? (
          <p className="p-4 text-sm text-slate-500">
            No dependency checks available (bridge may be disabled).
          </p>
        ) : (
          <ul className="divide-y divide-slate-800">
            {entries.map((e) => (
              <li
                key={e.name}
                className="flex items-center justify-between px-4 py-3"
              >
                <span className="text-sm font-medium text-slate-200">
                  {e.name}
                </span>
                <span className="flex items-center gap-3">
                  {e.latency != null && (
                    <span className="text-xs text-slate-500">
                      {e.latency.toFixed(1)} ms
                    </span>
                  )}
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      e.state === "ok" || e.state === "healthy" || e.state === "up"
                        ? "bg-green-900/60 text-green-300"
                        : "bg-red-900/60 text-red-300"
                    }`}
                  >
                    {String(e.state).toUpperCase()}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-4 flex gap-4 text-sm">
        <Link
          href="/dashboard/incidents"
          className="rounded-md border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800"
        >
          Open incidents →
        </Link>
        <button
          onClick={() => void load()}
          className="rounded-md border border-slate-700 px-3 py-1.5 text-slate-300 hover:bg-slate-800"
        >
          Refresh
        </button>
      </div>
    </div>
  );
}
