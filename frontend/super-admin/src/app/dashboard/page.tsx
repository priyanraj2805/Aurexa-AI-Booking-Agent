"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";

type Stats = {
  orgs_total: number;
  orgs_active: number;
  orgs_suspended: number;
  users_total: number;
  users_active: number;
  bookings_total: number;
  bookings_today: number;
  bookings_7d: number;
  conversations_total: number;
  conversations_active: number;
  conversations_today: number;
  alerts_open: number;
  alerts_critical: number;
  mrr: number | null;
  ai_tokens_30d: number | null;
  trend: { date: string; bookings: number; conversations: number }[];
};

type TopOrg = {
  id: string;
  name: string;
  is_active: boolean;
  booking_count: number;
  conversation_count: number;
};

type AuditRow = {
  id: string;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  created_at: string;
};

function KpiCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${accent ?? "text-white"}`}>
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [top, setTop] = useState<TopOrg[]>([]);
  const [recent, setRecent] = useState<AuditRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, t, a] = await Promise.all([
        api.get<Stats>("/tenants/stats"),
        api.get<{ items: TopOrg[] }>("/tenants/stats/top-orgs"),
        api.get<AuditRow[]>("/audit?limit=8"),
      ]);
      setStats(s);
      setTop(t.items);
      setRecent(a);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load dashboard");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (error) {
    return (
      <p className="text-sm text-red-400" role="alert">
        {error}
      </p>
    );
  }

  const maxTrend = stats
    ? Math.max(
        1,
        ...stats.trend.map((t) => Math.max(t.bookings, t.conversations))
      )
    : 1;

  return (
    <div>
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <p className="mt-1 text-sm text-slate-400">
        Platform overview — live counts from the tenant database.
      </p>

      <div className="mt-6 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard
          label="Active orgs"
          value={stats ? stats.orgs_active : "…"}
          sub={
            stats
              ? `${stats.orgs_suspended} suspended · ${stats.orgs_total} total`
              : ""
          }
        />
        <KpiCard
          label="Users"
          value={stats ? stats.users_active : "…"}
          sub={stats ? `${stats.users_total} total` : ""}
        />
        <KpiCard
          label="Bookings today"
          value={stats ? stats.bookings_today : "…"}
          sub={stats ? `${stats.bookings_7d} in last 7 days` : ""}
        />
        <KpiCard
          label="Active conversations"
          value={stats ? stats.conversations_active : "…"}
          sub={stats ? `${stats.conversations_today} started today` : ""}
        />
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Total bookings" value={stats ? stats.bookings_total : "…"} />
        <KpiCard
          label="Conversations"
          value={stats ? stats.conversations_total : "…"}
        />
        <KpiCard
          label="Open alerts"
          value={stats ? stats.alerts_open : "…"}
          accent={
            stats && stats.alerts_critical > 0 ? "text-red-400" : undefined
          }
          sub={
            stats && stats.alerts_critical > 0
              ? `${stats.alerts_critical} critical`
              : "all clear"
          }
        />
        <KpiCard label="MRR" value={stats ? (stats.mrr ?? "—") : "…"} sub="Plans & billing land in Phase 5" />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Activity — last 14 days
          </h2>
          <div className="mt-4 flex h-32 items-end gap-1">
            {stats?.trend.map((t) => (
              <div key={t.date} className="group flex flex-1 flex-col items-center">
                <div className="flex w-full flex-1 items-end justify-center gap-0.5">
                  <div
                    className="w-1/2 rounded-t bg-indigo-500/80"
                    style={{
                      height: `${Math.max(3, (t.bookings / maxTrend) * 100)}%`,
                    }}
                    title={`${t.date} — ${t.bookings} bookings`}
                  />
                  <div
                    className="w-1/2 rounded-t bg-slate-600/80"
                    style={{
                      height: `${Math.max(3, (t.conversations / maxTrend) * 100)}%`,
                    }}
                    title={`${t.date} — ${t.conversations} conversations`}
                  />
                </div>
                {stats.trend.indexOf(t) % 3 === 0 && (
                  <span className="mt-1 text-[9px] text-slate-500">
                    {t.date.slice(5)}
                  </span>
                )}
              </div>
            ))}
          </div>
          <div className="mt-3 flex gap-4 text-xs text-slate-400">
            <span className="flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-sm bg-indigo-500" />
              Bookings
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-2 w-2 rounded-sm bg-slate-600" />
              Conversations
            </span>
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Top orgs by bookings
          </h2>
          <div className="mt-3 space-y-2">
            {top.map((o, i) => (
              <Link
                key={o.id}
                href={`/dashboard/organizations/${o.id}`}
                className="flex items-center gap-3 rounded-md px-2 py-1.5 text-sm hover:bg-slate-800"
              >
                <span className="w-5 text-xs text-slate-500">{i + 1}</span>
                <span className="flex-1 truncate font-medium">{o.name}</span>
                <span className="text-xs text-slate-400">
                  {o.booking_count} bookings
                </span>
                <span className="text-xs text-slate-500">
                  {o.conversation_count} conv
                </span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                    o.is_active
                      ? "bg-green-900/60 text-green-300"
                      : "bg-red-900/60 text-red-300"
                  }`}
                >
                  {o.is_active ? "ON" : "OFF"}
                </span>
              </Link>
            ))}
            {top.length === 0 && (
              <p className="py-6 text-center text-sm text-slate-500">
                No organizations yet
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-slate-800 bg-slate-900/50 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Recent admin actions
          </h2>
          <Link
            href="/dashboard/audit"
            className="text-xs text-indigo-400 hover:underline"
          >
            View full audit log →
          </Link>
        </div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-2 py-2">Time</th>
                <th className="px-2 py-2">Actor</th>
                <th className="px-2 py-2">Action</th>
                <th className="px-2 py-2">Resource</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800">
              {recent.map((r) => (
                <tr key={r.id}>
                  <td className="px-2 py-2 text-slate-400">
                    {new Date(r.created_at).toLocaleString()}
                  </td>
                  <td className="px-2 py-2">{r.actor_email ?? "—"}</td>
                  <td className="px-2 py-2">
                    <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs">
                      {r.action}
                    </span>
                  </td>
                  <td className="px-2 py-2 text-slate-300">
                    {r.resource_type}
                    {r.resource_id ? ` · ${r.resource_id.slice(0, 8)}…` : ""}
                  </td>
                </tr>
              ))}
              {recent.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-2 py-6 text-center text-slate-500">
                    No admin actions yet
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
