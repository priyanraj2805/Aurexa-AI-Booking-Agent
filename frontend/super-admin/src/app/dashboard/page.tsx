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
  trend: number[];
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
      <p className={`mt-1 text-2xl font-semibold ${accent ?? "text-slate-100"}`}>
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
  );
}

const RANGES = [7, 14, 30];

const PIE_COLORS = {
  bookings: "var(--i500)",
  conversations: "var(--s600)",
};

const LINE_COLORS = [
  "var(--i500)",
  "var(--e300)",
  "var(--sk300)",
  "var(--a300)",
  "var(--p300)",
];

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [top, setTop] = useState<TopOrg[]>([]);
  const [recent, setRecent] = useState<AuditRow[]>([]);
  const [range, setRange] = useState(7);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (days: number) => {
    setError(null);
    try {
      const [s, t, a] = await Promise.all([
        api.get<Stats>(`/tenants/stats?days=${days}`),
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
    load(range);
  }, [load, range]);

  if (error) {
    return (
      <p className="text-sm text-red-400" role="alert">
        {error}
      </p>
    );
  }

  const bookingsTotal = stats
    ? stats.trend.reduce((s, t) => s + t.bookings, 0)
    : 0;
  const conversationsTotal = stats
    ? stats.trend.reduce((s, t) => s + t.conversations, 0)
    : 0;
  const activityTotal = bookingsTotal + conversationsTotal;

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
          label="MRR"
          value={
            stats?.mrr != null
              ? `$${(stats.mrr / 100).toLocaleString("en-US", {
                  maximumFractionDigits: 0,
                })}`
              : "—"
          }
        />
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="flex h-full flex-col rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
              Activity — last {range} days
            </h2>
            <select
              value={range}
              onChange={(e) => setRange(Number(e.target.value))}
              className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 outline-none focus:border-indigo-500"
            >
              {RANGES.map((d) => (
                <option key={d} value={d}>
                  Last {d} days
                </option>
              ))}
            </select>
          </div>
          <div className="mt-4 flex flex-1 flex-col items-center justify-center overflow-hidden">
            {activityTotal > 0 ? (
              <>
                <svg viewBox="0 0 160 160" className="h-48 w-48 shrink-0">
                  <g transform="rotate(-90 80 80)">
                    <circle
                      cx="80"
                      cy="80"
                      r="62"
                      fill="none"
                      stroke="rgba(148,163,184,0.12)"
                      strokeWidth="28"
                    />
                    {[
                      {
                        value: bookingsTotal,
                        color: PIE_COLORS.bookings,
                      },
                      {
                        value: conversationsTotal,
                        color: PIE_COLORS.conversations,
                      },
                    ].reduce<React.ReactElement[]>(
                      (els, slice, i, arr) => {
                        const before = arr
                          .slice(0, i)
                          .reduce((s, x) => s + x.value, 0);
                        const frac = slice.value / activityTotal;
                        const C = 2 * Math.PI * 62;
                        els.push(
                          <circle
                            key={i}
                            cx="80"
                            cy="80"
                            r="62"
                            fill="none"
                            stroke={slice.color}
                            strokeWidth="28"
                            strokeDasharray={`${frac * C} ${C - frac * C}`}
                            strokeDashoffset={-((before / activityTotal) * C)}
                          />
                        );
                        return els;
                      },
                      []
                    )}
                  </g>
                  <text
                    x="80"
                    y="82"
                    textAnchor="middle"
                    fill="var(--s100)"
                    style={{ fontSize: 24, fontWeight: 700 }}
                  >
                    {activityTotal}
                  </text>
                  <text
                    x="80"
                    y="100"
                    textAnchor="middle"
                    fill="var(--s400)"
                    style={{ fontSize: 11 }}
                  >
                    total
                  </text>
                </svg>
                <div className="mt-4 flex w-full flex-col gap-1.5 text-xs text-slate-400">
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-2 w-2 rounded-sm bg-indigo-500" />
                    <span className="flex-1">Bookings</span>
                    <span className="font-medium text-slate-300">
                      {bookingsTotal}
                    </span>
                    <span className="w-12 text-right">
                      {activityTotal
                        ? Math.round((bookingsTotal / activityTotal) * 100)
                        : 0}
                      %
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-2 w-2 rounded-sm bg-slate-600" />
                    <span className="flex-1">Conversations</span>
                    <span className="font-medium text-slate-300">
                      {conversationsTotal}
                    </span>
                    <span className="w-12 text-right">
                      {activityTotal
                        ? Math.round((conversationsTotal / activityTotal) * 100)
                        : 0}
                      %
                    </span>
                  </span>
                </div>
              </>
            ) : (
              <p className="py-10 text-center text-sm text-slate-500">
                No activity in the last {range} days
              </p>
            )}
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
