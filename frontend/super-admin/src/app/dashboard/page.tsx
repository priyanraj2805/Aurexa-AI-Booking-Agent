"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import {
  Building2,
  Users,
  CalendarCheck,
  MessageSquare,
  TrendingUp,
  TrendingDown,
  Activity,
  ScrollText,
  UserPlus,
  UserMinus,
  Ban,
  ShieldCheck,
  KeyRound,
  Database,
  Megaphone,
  CreditCard,
  Wrench,
  LogIn,
  LogOut,
  BadgeCheck,
  type LucideIcon,
} from "lucide-react";
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

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

function todayLabel(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

function trendPct(values: number[]): number | null {
  if (values.length < 4) return null;
  const mid = Math.floor(values.length / 2);
  const prev = values.slice(0, mid).reduce((s, v) => s + v, 0);
  const curr = values.slice(mid).reduce((s, v) => s + v, 0);
  if (prev === 0) return curr > 0 ? 100 : null;
  return Math.round(((curr - prev) / prev) * 100);
}

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 7) return `${d}d ago`;
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
  });
}

const WINDOW_SIZES = [7, 14, 30];

function Sparkline({
  data,
  color,
  w = 96,
  h = 28,
}: {
  data: number[];
  color: string;
  w?: number;
  h?: number;
}) {
  const points = data.filter((n) => n >= 0);
  if (points.length < 2) return null;
  const max = Math.max(...points, 1);
  const pts = points
    .map((v, i) => `${(i / (points.length - 1)) * w},${h - (v / max) * (h - 2) - 1}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="overflow-visible shrink-0" aria-hidden>
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.9"
      />
    </svg>
  );
}

type StatCardProps = {
  title: string;
  value: string | number;
  icon: LucideIcon;
  chipClass: string;
  href?: string;
  spark?: number[];
  sparkColor?: string;
  trend?: number | null;
  sub?: string;
};

function StatCard({
  title,
  value,
  icon: Icon,
  chipClass,
  href,
  spark,
  sparkColor,
  trend,
  sub,
}: StatCardProps) {
  const inner = (
    <>
      <div className="absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-transparent via-indigo-500/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
      <div className="flex items-center gap-3">
        <span
          className={`flex items-center justify-center size-11 rounded-xl shrink-0 ${chipClass}`}
        >
          <Icon className="size-5" />
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-medium text-slate-400">{title}</div>
          <div className="flex items-baseline gap-2">
            <span className="text-[26px] font-bold tracking-tight leading-none text-slate-100">
              {value}
            </span>
            {trend != null && trend !== 0 && (
              <span
                className={`inline-flex items-center gap-0.5 text-[11px] font-semibold ${
                  trend > 0 ? "text-green-400" : "text-red-400"
                }`}
              >
                {trend > 0 ? (
                  <TrendingUp className="size-3" />
                ) : (
                  <TrendingDown className="size-3" />
                )}
                {trend > 0 ? "+" : ""}
                {trend}%
              </span>
            )}
          </div>
          {sub && <div className="text-[11px] text-slate-500 mt-0.5">{sub}</div>}
        </div>
        {spark && sparkColor && <Sparkline data={spark} color={sparkColor} />}
      </div>
    </>
  );

  const cls =
    "group relative overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/50 p-5 transition-all hover:border-indigo-500/40 hover:shadow-lg hover:shadow-black/20";

  if (href) {
    return (
      <Link href={href} className={cls}>
        {inner}
      </Link>
    );
  }
  return <div className={cls}>{inner}</div>;
}

const ACTION_META: Record<
  string,
  { label: string; icon: LucideIcon; cls: string }
> = {
  "org.create": { label: "Created organization", icon: Building2, cls: "bg-emerald-900/40 text-emerald-300" },
  "org.update": { label: "Updated organization", icon: Building2, cls: "bg-sky-900/40 text-sky-300" },
  "org.suspend": { label: "Suspended organization", icon: Ban, cls: "bg-red-900/40 text-red-300" },
  "org.activate": { label: "Activated organization", icon: BadgeCheck, cls: "bg-green-900/40 text-green-300" },
  "org.delete": { label: "Deleted organization", icon: Ban, cls: "bg-red-900/40 text-red-300" },
  "user.create": { label: "Created user", icon: UserPlus, cls: "bg-emerald-900/40 text-emerald-300" },
  "user.update": { label: "Updated user", icon: Users, cls: "bg-sky-900/40 text-sky-300" },
  "user.suspend": { label: "Suspended user", icon: UserMinus, cls: "bg-amber-900/40 text-amber-300" },
  "user.delete": { label: "Deleted user", icon: UserMinus, cls: "bg-red-900/40 text-red-300" },
  "user.role_change": { label: "Changed user role", icon: ShieldCheck, cls: "bg-purple-900/40 text-purple-300" },
  "plan.create": { label: "Created plan", icon: CreditCard, cls: "bg-emerald-900/40 text-emerald-300" },
  "plan.update": { label: "Updated plan", icon: CreditCard, cls: "bg-sky-900/40 text-sky-300" },
  "billing.update": { label: "Updated billing", icon: CreditCard, cls: "bg-sky-900/40 text-sky-300" },
  "broadcast.send": { label: "Sent broadcast", icon: Megaphone, cls: "bg-amber-900/40 text-amber-300" },
  "settings.update": { label: "Updated settings", icon: Wrench, cls: "bg-sky-900/40 text-sky-300" },
  "security.update": { label: "Updated security", icon: KeyRound, cls: "bg-purple-900/40 text-purple-300" },
  "login": { label: "Signed in", icon: LogIn, cls: "bg-emerald-900/40 text-emerald-300" },
  "logout": { label: "Signed out", icon: LogOut, cls: "bg-slate-800 text-slate-300" },
};

function humanizeAction(action: string): { label: string; icon: LucideIcon; cls: string } {
  const meta = ACTION_META[action.toLowerCase()];
  if (meta) return meta;
  const parts = action.split(/[._-]/).filter(Boolean);
  const label =
    parts.length > 1
      ? `${parts[1].charAt(0).toUpperCase()}${parts[1].slice(1).toLowerCase()} ${parts[0].toLowerCase()}`
      : action;
  return { label, icon: Wrench, cls: "bg-slate-800 text-slate-300" };
}

const RANGES = [7, 14, 30];

const PIE_COLORS = {
  bookings: "var(--i500)",
  conversations: "var(--e300)",
};

export default function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [top, setTop] = useState<TopOrg[]>([]);
  const [recent, setRecent] = useState<AuditRow[]>([]);
  const [range, setRange] = useState(7);
  const MAX_FETCH_DAYS = 30;
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, t, a] = await Promise.all([
        api.get<Stats>(`/tenants/stats?days=${MAX_FETCH_DAYS}`),
        api.get<{ items: TopOrg[] }>("/tenants/stats/top-orgs"),
        api.get<{ items: AuditRow[] }>("/audit?page=1&page_size=8"),
      ]);
      setStats(s);
      setTop(t.items);
      setRecent(a.items);
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

  const bookingsSeries = (stats?.trend ?? []).map((t) => t.bookings);
  const convSeries = (stats?.trend ?? []).map((t) => t.conversations);
  const bookingsTrend = trendPct(bookingsSeries);
  const convTrend = trendPct(convSeries);

  const bookingsTotal = bookingsSeries.reduce((s, v) => s + v, 0);
  const conversationsTotal = convSeries.reduce((s, v) => s + v, 0);
  const activityTotal = bookingsTotal + conversationsTotal;

  const fullAreaData = (stats?.trend ?? []).map((t) => ({
    date: t.date,
    Bookings: t.bookings,
    Conversations: t.conversations,
  }));

  const areaData = fullAreaData.slice(-range);

  const chartScrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = chartScrollRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [range, stats]);

  const hasActivity = activityTotal > 0;

  const chart = {
    grid: "var(--s800)",
    tick: "var(--s400)",
    bookings: PIE_COLORS.bookings,
    conversations: PIE_COLORS.conversations,
    tooltipBg: "var(--s900)",
    tooltipBorder: "var(--s700)",
  };

  const chartTooltip = {
    contentStyle: {
      backgroundColor: chart.tooltipBg,
      border: `1px solid ${chart.tooltipBorder}`,
      borderRadius: "10px",
      fontSize: "12px",
    },
    labelStyle: { color: chart.tick, marginBottom: "4px" },
  };

  const rankCls = (i: number) =>
    i === 0
      ? "bg-amber-900/60 text-amber-300"
      : i === 1
        ? "bg-slate-700/60 text-slate-200"
        : i === 2
          ? "bg-orange-900/50 text-orange-300"
          : "bg-slate-800 text-slate-400";

  return (
    <div>
      <div>
        <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">
          {greeting()}, welcome back
        </h1>
        <p className="mt-1.5 text-xs sm:text-sm text-slate-400">
          Platform overview · {todayLabel()}
        </p>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          title="Active Orgs"
          value={stats ? stats.orgs_active : "…"}
          icon={Building2}
          chipClass="bg-indigo-500/15 text-indigo-400"
          href="/dashboard/organizations"
          sub={stats ? `${stats.orgs_suspended} suspended · ${stats.orgs_total} total` : ""}
        />
        <StatCard
          title="Users"
          value={stats ? stats.users_active : "…"}
          icon={Users}
          chipClass="bg-sky-500/15 text-sky-300"
          href="/dashboard/users"
          sub={stats ? `${stats.users_total} total` : ""}
        />
        <StatCard
          title="Bookings Today"
          value={stats ? stats.bookings_today : "…"}
          icon={CalendarCheck}
          chipClass="bg-emerald-500/15 text-emerald-300"
          spark={bookingsSeries}
          sparkColor={chart.bookings}
          trend={bookingsTrend}
          sub={stats ? `${stats.bookings_7d} in last 7 days` : ""}
        />
        <StatCard
          title="Active Conversations"
          value={stats ? stats.conversations_active : "…"}
          icon={MessageSquare}
          chipClass="bg-purple-500/15 text-purple-300"
          spark={convSeries}
          sparkColor={chart.conversations}
          trend={convTrend}
          sub={stats ? `${stats.conversations_today} started today` : ""}
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-10 gap-y-4 rounded-2xl border border-slate-800 bg-slate-900/50 px-6 py-4">
        <div>
          <p className="text-[11px] uppercase tracking-wide text-slate-400">Lifetime bookings</p>
          <p className="mt-0.5 text-lg font-bold text-slate-100">
            {stats ? stats.bookings_total.toLocaleString() : "…"}
          </p>
        </div>
        <div className="hidden h-8 w-px bg-slate-800 sm:block" />
        <div>
          <p className="text-[11px] uppercase tracking-wide text-slate-400">Lifetime conversations</p>
          <p className="mt-0.5 text-lg font-bold text-slate-100">
            {stats ? stats.conversations_total.toLocaleString() : "…"}
          </p>
        </div>
        {stats?.ai_tokens_30d != null && (
          <>
            <div className="hidden h-8 w-px bg-slate-800 sm:block" />
            <div>
              <p className="text-[11px] uppercase tracking-wide text-slate-400">AI tokens (30d)</p>
              <p className="mt-0.5 text-lg font-bold text-slate-100">
                {stats.ai_tokens_30d.toLocaleString()}
              </p>
            </div>
          </>
        )}
        <div className="ml-auto text-right">
          <p className="text-[11px] uppercase tracking-wide text-slate-400">MRR</p>
          <p className="mt-0.5 text-xl font-bold text-emerald-300">
            {stats?.mrr != null
              ? `$${(stats.mrr / 100).toLocaleString("en-US", { maximumFractionDigits: 0 })}`
              : "—"}
          </p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-5 lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-3">
              <span className="flex items-center justify-center size-9 rounded-lg bg-indigo-500/15 text-indigo-400 shrink-0">
                <Activity className="size-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold leading-tight text-slate-100">
                  Activity Overview
                </h2>
                <p className="text-[11px] text-slate-400">
                  Bookings vs conversations · {range}-day window
                </p>
              </div>
            </div>
            <select
              value={range}
              onChange={(e) => {
                setRange(Number(e.target.value));
              }}
              className="rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 outline-none focus:border-indigo-500"
            >
              {WINDOW_SIZES.map((d) => (
                <option key={d} value={d}>
                  {d} days
                </option>
              ))}
            </select>
          </div>
          <div
            ref={chartScrollRef}
            className="mt-4 h-64 overflow-x-auto overflow-y-hidden scrollbar-thin"
          >
            {hasActivity ? (
              <div
                className="h-full"
                style={{ width: `${(areaData.length / 7) * 100}%`, minWidth: "100%" }}
              >
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={areaData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                  <defs>
                    <linearGradient id="saGradBookings" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={chart.bookings} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={chart.bookings} stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="saGradConversations" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={chart.conversations} stopOpacity={0.3} />
                      <stop offset="100%" stopColor={chart.conversations} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke={chart.grid} strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11, fill: chart.tick }}
                    tickLine={false}
                    axisLine={{ stroke: chart.grid }}
                    tickFormatter={(v: string) => {
                      const d = new Date(`${v}T00:00:00`);
                      return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
                    }}
                    minTickGap={24}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: chart.tick }}
                    tickLine={false}
                    axisLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip {...chartTooltip} />
                  <Area
                    type="monotone"
                    dataKey="Bookings"
                    stroke={chart.bookings}
                    strokeWidth={2.5}
                    fill="url(#saGradBookings)"
                    animationDuration={1500}
                  />
                  <Area
                    type="monotone"
                    dataKey="Conversations"
                    stroke={chart.conversations}
                    strokeWidth={2}
                    fill="url(#saGradConversations)"
                    animationDuration={1500}
                  />
                </AreaChart>
              </ResponsiveContainer>
              </div>
            ) : (
              <div className="flex h-full flex-col items-center justify-center text-center">
                <Activity className="size-8 text-slate-600 mb-2" />
                <p className="text-sm text-slate-500">
                  No activity in the last {range} days
                </p>
              </div>
            )}
          </div>
        </div>

        <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
          <div className="flex items-center gap-3 mb-2">
            <span className="flex items-center justify-center size-9 rounded-lg bg-purple-500/15 text-purple-300 shrink-0">
              <CalendarCheck className="size-4" />
            </span>
            <div>
              <h2 className="text-sm font-semibold leading-tight text-slate-100">
                Activity Split
              </h2>
              <p className="text-[11px] text-slate-400">Last {range} days</p>
            </div>
          </div>
          <div className="flex flex-1 flex-col items-center justify-center overflow-hidden">
            {hasActivity ? (
              <>
                <div className="relative h-44 w-44 shrink-0">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={[
                          { name: "Bookings", value: bookingsTotal },
                          { name: "Conversations", value: conversationsTotal },
                        ]}
                        cx="50%"
                        cy="50%"
                        innerRadius={48}
                        outerRadius={66}
                        paddingAngle={3}
                        dataKey="value"
                        strokeWidth={0}
                        startAngle={90}
                        endAngle={-270}
                        animationBegin={0}
                        animationDuration={1800}
                        animationEasing="ease-out"
                      >
                        <Cell fill={PIE_COLORS.bookings} style={{ outline: "none" }} />
                        <Cell fill={PIE_COLORS.conversations} style={{ outline: "none" }} />
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                    <span
                      className="text-slate-100"
                      style={{ fontSize: 24, fontWeight: 700 }}
                    >
                      {activityTotal}
                    </span>
                    <span className="text-slate-400" style={{ fontSize: 11 }}>
                      total
                    </span>
                  </div>
                </div>
                <div className="mt-3 flex w-full flex-col gap-1.5 text-xs text-slate-400">
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-2 w-2 rounded-sm bg-indigo-500" />
                    <span className="flex-1">Bookings</span>
                    <span className="font-medium text-slate-300">{bookingsTotal}</span>
                    <span className="w-12 text-right">
                      {Math.round((bookingsTotal / activityTotal) * 100)}%
                    </span>
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="inline-block h-2 w-2 rounded-sm bg-emerald-400" />
                    <span className="flex-1">Conversations</span>
                    <span className="font-medium text-slate-300">{conversationsTotal}</span>
                    <span className="w-12 text-right">
                      {Math.round((conversationsTotal / activityTotal) * 100)}%
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
      </div>

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-5">
          <h2 className="text-sm font-semibold text-slate-100">Top orgs by bookings</h2>
          <p className="mt-0.5 text-[11px] text-slate-400">Ranked by booking volume</p>
          <div className="mt-3 space-y-1.5">
            {top.map((o, i) => (
              <Link
                key={o.id}
                href={`/dashboard/organizations/${o.id}`}
                className="group flex items-center gap-3 rounded-xl px-2 py-2 text-sm transition-colors hover:bg-slate-800/70"
              >
                <span
                  className={`flex size-6 shrink-0 items-center justify-center rounded-md text-[11px] font-bold ${rankCls(i)}`}
                >
                  {i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-slate-200 group-hover:text-slate-100">
                    {o.name}
                  </p>
                  <p className="text-[11px] text-slate-500">
                    {o.booking_count} bookings · {o.conversation_count} conv
                  </p>
                </div>
                <Sparkline data={o.trend} color="var(--i400)" w={56} h={20} />
                <span
                  className={`shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
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

        <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-5 lg:col-span-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <span className="flex items-center justify-center size-9 rounded-lg bg-amber-500/15 text-amber-300 shrink-0">
                <ScrollText className="size-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold leading-tight text-slate-100">
                  Recent admin actions
                </h2>
                <p className="text-[11px] text-slate-400">Latest platform activity</p>
              </div>
            </div>
            <Link
              href="/dashboard/audit"
              className="text-xs text-indigo-400 hover:underline"
            >
              View full audit log →
            </Link>
          </div>
          <div className="mt-3 divide-y divide-slate-800">
            {recent.map((r) => {
              const meta = humanizeAction(r.action);
              const Icon = meta.icon;
              return (
                <div key={r.id} className="flex items-center gap-3 py-2.5">
                  <span
                    className={`flex size-8 shrink-0 items-center justify-center rounded-lg ${meta.cls}`}
                  >
                    <Icon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-200">
                      {meta.label}
                    </p>
                    <p className="truncate text-[11px] text-slate-500">
                      {r.actor_email ?? "system"} · {r.resource_type}
                      {r.resource_id ? ` · ${r.resource_id.slice(0, 8)}…` : ""}
                    </p>
                  </div>
                  <span
                    className="shrink-0 text-xs text-slate-400"
                    title={new Date(r.created_at).toLocaleString()}
                  >
                    {relTime(r.created_at)}
                  </span>
                </div>
              );
            })}
            {recent.length === 0 && (
              <p className="py-6 text-center text-sm text-slate-500">
                No admin actions yet
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
