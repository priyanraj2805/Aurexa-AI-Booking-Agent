"use client";

import { useEffect, useRef, useState } from "react";

import {
  CalendarCheck,
  CheckCircle2,
  MessageSquare,
  CalendarX2,
  Loader2,
  AlertCircle,
  Rocket,
  ArrowRight,
  Circle,
  TrendingUp,
  TrendingDown,
  Activity,
  BarChart3,
} from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { toast } from "sonner";
import { AvatarInitials } from "@/components/ui/status-badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { HoverCard, HoverCardTrigger, HoverCardContent } from "@/components/ui/hover-card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { MoreHorizontal, Copy, Mail, Phone } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import {
  useStats,
  useRecentBookings,
  useOnboardingStatus,
  type Stats,
  type DailyCount,
} from "@/hooks/use-dashboard";

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

const WINDOW_SIZES = [7, 14, 30];

function trendPct(arr: DailyCount[] | undefined): number | null {
  if (!arr || arr.length < 14) return null;
  const prev = arr.slice(0, 7).reduce((s, d) => s + d.count, 0);
  const curr = arr.slice(7).reduce((s, d) => s + d.count, 0);
  if (prev === 0) return curr > 0 ? 100 : 0;
  return Math.round(((curr - prev) / prev) * 100);
}

function Sparkline({ data, color }: { data: number[]; color: string }) {
  const points = data.filter((n) => n >= 0);
  if (points.length < 2) return null;
  const max = Math.max(...points, 1);
  const w = 80;
  const h = 28;
  const pts = points
    .map((v, i) => `${(i / (points.length - 1)) * w},${h - (v / max) * (h - 2) - 1}`)
    .join(" ");
  return (
    <svg width={w} height={h} className="shrink-0" aria-hidden>
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

function MiniTrendArea({
  data,
  color,
  tooltipBg,
  tooltipBorder,
  tickColor,
}: {
  data: { date: string; count: number }[];
  color: string;
  tooltipBg: string;
  tooltipBorder: string;
  tickColor: string;
}) {
  if (data.length < 2) return null;
  const id = `gradQuick_${color.replace(/[^a-zA-Z0-9]/g, "")}`;
  return (
    <div className="h-20 w-full -mx-1">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Tooltip
            contentStyle={{
              backgroundColor: tooltipBg,
              border: `1px solid ${tooltipBorder}`,
              borderRadius: "8px",
              fontSize: "11px",
            }}
            labelStyle={{ color: tickColor, marginBottom: "2px" }}
            formatter={(value: number | string) => [`${value}`, "Bookings"]}
            labelFormatter={(label: string) => {
              const d = new Date(`${label}T00:00:00`);
              return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
            }}
          />
          <Area
            type="monotone"
            dataKey="count"
            stroke={color}
            strokeWidth={2}
            fill={`url(#${id})`}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

function StatMiniBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.min((value / max) * 100, 100) : 0;
  return (
    <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, backgroundColor: color, minWidth: pct > 0 ? "4px" : "0" }}
      />
    </div>
  );
}

type StatCardProps = {
  title: string;
  value: number;
  icon: typeof CalendarCheck;
  chipClass: string;
  href: string;
  spark: number[];
  sparkColor: string;
  trend: number | null;
  badge?: string | null;
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
  badge,
}: StatCardProps) {
  return (
    <Link
      href={href}
      className="group relative overflow-hidden rounded-2xl border border-border bg-card p-5 transition-all hover:border-primary/40 hover:shadow-lg hover:shadow-black/5 dark:hover:shadow-black/40"
    >
      <div className="absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-transparent via-primary/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
      <div className="flex items-center gap-3">
        <span className={`flex items-center justify-center size-11 rounded-xl shrink-0 ${chipClass}`}>
          <Icon className="size-5" />
        </span>
        <div className="flex-1 min-w-0 overflow-hidden">
          <div className="text-[13px] font-medium text-muted-foreground">{title}</div>
          <div className="flex items-baseline gap-2">
            <span className="text-[28px] font-bold tracking-tight leading-none">{value}</span>
            {trend !== null && trend !== 0 && (
              <span
                className={`inline-flex items-center gap-0.5 text-[11px] font-semibold whitespace-nowrap ${
                  trend > 0 ? "text-green-500" : "text-red-500"
                }`}
              >
                {trend > 0 ? <TrendingUp className="size-3" /> : <TrendingDown className="size-3" />}
                {trend > 0 ? "+" : ""}
                {trend}%
              </span>
            )}
          </div>
          {badge && <div className="text-[11px] text-amber-500 mt-0.5">{badge}</div>}
        </div>
        <Sparkline data={spark} color={sparkColor} />
      </div>
    </Link>
  );
}

function DonutChart({
  booked,
  cancelled,
}: {
  booked: number;
  cancelled: number;
}) {
  const total = booked + cancelled;
  if (total === 0) return null;

  const bookedPct = Math.round((booked / total) * 100);
  const cancelledPct = 100 - bookedPct;

  const data = [
    { name: "Bookings", value: booked },
    { name: "Cancelled", value: cancelled },
  ];

  return (
    <div className="flex flex-col items-center">
      <div className="relative h-48 w-48">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              cx="50%"
              cy="50%"
              innerRadius={50}
              outerRadius={70}
              paddingAngle={3}
              dataKey="value"
              strokeWidth={0}
              startAngle={90}
              endAngle={-270}
              animationBegin={0}
              animationDuration={1800}
              animationEasing="ease-out"
            >
              {data.map((_, i) => (
                <Cell
                  key={i}
                  fill={i === 0 ? "var(--i500)" : "var(--s600)"}
                  style={{ outline: "none" }}
                />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          <span className="text-3xl font-bold tracking-tight">{total}</span>
          <span className="text-[11px] text-muted-foreground">total</span>
        </div>
      </div>
      <div className="mt-4 flex w-full flex-col gap-2 text-xs text-muted-foreground">
        <Link
          href="/dashboard/bookings?status=CONFIRMED"
          className="group flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-secondary/50"
        >
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-[var(--i500)]" />
          <span className="flex-1 font-medium">Bookings</span>
          <span className="font-semibold text-foreground">{booked}</span>
          <span className="w-10 text-right text-muted-foreground">{bookedPct}%</span>
        </Link>
        <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden">
          <div className="h-full rounded-full bg-[var(--i500)]" style={{ width: `${bookedPct}%` }} />
        </div>
        <Link
          href="/dashboard/bookings/cancelled"
          className="group flex items-center gap-2 rounded-lg px-2 py-1.5 transition-colors hover:bg-secondary/50"
        >
          <span className="inline-block h-2.5 w-2.5 rounded-sm bg-[var(--s600)]" />
          <span className="flex-1 font-medium">Cancelled</span>
          <span className="font-semibold text-foreground">{cancelled}</span>
          <span className="w-10 text-right text-muted-foreground">{cancelledPct}%</span>
        </Link>
        <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden">
          <div className="h-full rounded-full bg-[var(--s600)]" style={{ width: `${cancelledPct}%` }} />
        </div>
      </div>
    </div>
  );
}

function CardHeader({
  icon,
  title,
  subtitle,
  iconClass,
  action,
}: {
  icon: typeof Activity;
  title: string;
  subtitle?: string;
  iconClass: string;
  action?: React.ReactNode;
}) {
  const Icon = icon;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
      <div className="flex items-center gap-3">
        <span className={`flex items-center justify-center size-9 rounded-lg shrink-0 ${iconClass}`}>
          <Icon className="size-4" />
        </span>
        <div>
          <h3 className="text-sm font-semibold leading-tight">{title}</h3>
          {subtitle && <p className="text-[11px] text-muted-foreground">{subtitle}</p>}
        </div>
      </div>
      {action && <div className="flex items-center">{action}</div>}
    </div>
  );
}



function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function getInitials(name: string): string {
  return name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
}

/** Deterministic avatar tone from the name — stable across re-renders. */
function nameTone(name: string): "primary" | "red" {
  let hash = 0;
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return hash % 2 === 0 ? "primary" : "red";
}

const SUPPORTED_CHANNELS = ["web", "whatsapp"];

function ChannelBreakdown({
  data,
  colors,
}: {
  data: { name: string; count: number }[];
  colors: string[];
}) {
  const merged = SUPPORTED_CHANNELS.map((ch) => ({
    name: ch,
    count: data.find((d) => d.name === ch)?.count ?? 0,
  })).sort((a, b) => b.count - a.count);

  const total = merged.reduce((s, d) => s + d.count, 0);
  if (total === 0) return null;

  const channelIcons: Record<string, string> = {
    web: "🌐",
    whatsapp: "💬",
  };

  return (
    <div className="flex flex-col h-full">
      <div className="text-center pb-4">
        <div className="text-4xl font-bold tracking-tight">{total}</div>
        <div className="text-xs text-muted-foreground mt-0.5">total confirmed</div>
      </div>
      <div className="flex-1 space-y-4">
        {merged.map((d, i) => {
          const pct = total > 0 ? (d.count / total) * 100 : 0;
          const color = colors[i % colors.length];
          return (
            <div key={d.name} className="space-y-2">
              <div className="flex items-center justify-between text-sm">
                <div className="flex items-center gap-2.5">
                  <span className="text-base">{channelIcons[d.name] || "📊"}</span>
                  <span className="font-medium capitalize">{d.name}</span>
                </div>
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded-md bg-secondary text-[10px] font-semibold">
                    {Math.round(pct)}%
                  </span>
                  <span className="font-semibold text-foreground">{d.count}</span>
                </div>
              </div>
              <div className="h-2.5 bg-secondary rounded-full overflow-hidden">
                <div
                  className="h-full rounded-full transition-all duration-700 ease-out"
                  style={{
                    width: `${pct}%`,
                    backgroundColor: color,
                    minWidth: pct > 0 ? "12px" : "0px",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
      <div className="pt-4 mt-auto border-t border-border">
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span>{merged.length} channels</span>
          <span>Most: <span className="font-medium text-foreground capitalize">{merged[0]?.name || "—"}</span></span>
        </div>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const { resolvedTheme } = useTheme();
  const dark = resolvedTheme !== "light";
  const [range, setRange] = useState(7);
  const MAX_FETCH_DAYS = 30;
  const { data: stats, loading: statsLoading, error: statsError } = useStats(MAX_FETCH_DAYS);
  const { data: bookings, loading: bookingsLoading, error: bookingsError } = useRecentBookings(10);
  const { data: onboarding, loading: onboardingLoading } = useOnboardingStatus();

  const chartColors = dark
    ? {
        grid: "#27272a",
        tick: "#a1a1aa",
        primary: "#3b82f6",
        indigo: "#6366f1",
        blue: "#3b82f6",
        cancelled: "#ef4444",
        green: "#10b981",
        red: "#ef4444",
        amber: "#f59e0b",
        violet: "#8b5cf6",
        tooltipBg: "#18181b",
        tooltipBorder: "#27272a",
      }
    : {
        grid: "#e5e5e5",
        tick: "#71717a",
        primary: "#ef5d25",
        indigo: "#ef5d25",
        blue: "#3b82f6",
        cancelled: "#3b82f6",
        green: "#10b981",
        red: "#ef4444",
        amber: "#f59e0b",
        violet: "#8b5cf6",
        tooltipBg: "#ffffff",
        tooltipBorder: "#e5e5e5",
      };

  const bookingsTrend = trendPct(stats?.bookings_by_day);
  const cancelledTrend = trendPct(stats?.cancelled_by_day);

  const fullAreaData = (stats?.bookings_by_day ?? []).map((d, i) => ({
    date: d.date,
    Bookings: d.count,
    Cancelled: stats?.cancelled_by_day?.[i]?.count ?? 0,
  }));

  const areaData = fullAreaData.slice(-range);

  const chartScrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = chartScrollRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [range, statsLoading]);

  const barData = (stats?.channel_breakdown ?? []).map((c) => ({
    name: c.channel,
    count: c.count,
  }));
  const barColors = [
    chartColors.indigo,
    chartColors.blue,
    chartColors.green,
    chartColors.amber,
    chartColors.violet,
    chartColors.red,
  ];

  const hasActivity =
    (stats?.total_bookings ?? 0) + (stats?.cancelled_sessions ?? 0) > 0;

  const showOnboarding = onboarding && onboarding.completed_count < onboarding.total_count;

  const chartTooltip = {
    contentStyle: {
      backgroundColor: chartColors.tooltipBg,
      border: `1px solid ${chartColors.tooltipBorder}`,
      borderRadius: "10px",
      fontSize: "12px",
    },
    labelStyle: { color: chartColors.tick, marginBottom: "4px" },
  };

  const cardIcon = "bg-primary/10 text-primary";
  const confirmedIcon = "bg-green-500/15 text-green-500";
  const cancelledIcon = "bg-red-500/15 text-red-500";
  const activeIcon = "bg-blue-500/15 text-blue-500";
  const indigoIcon = "bg-primary/15 text-primary";

  return (
    <>
      <div className="flex flex-wrap items-end justify-between gap-3 mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">
              {greeting()}, welcome back
            </h1>
            <span className="relative flex size-2.5 mb-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75" />
              <span className="relative inline-flex rounded-full size-2.5 bg-green-500" />
            </span>
          </div>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1.5">{todayLabel()}</p>
        </div>
      </div>

      {/* Onboarding Checklist */}
      {!onboardingLoading && showOnboarding && (
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6 mb-5">
          <div className="flex items-center gap-3 mb-4">
            <div className="flex items-center justify-center size-10 rounded-xl bg-primary/10 text-primary">
              <Rocket className="size-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold">Getting Started</h3>
              <p className="text-muted-foreground text-xs">
                Complete these steps to launch your booking agent —{" "}
                {onboarding.completed_count}/{onboarding.total_count} done
              </p>
            </div>
          </div>

          <div className="w-full h-2 bg-secondary rounded-full mb-5 overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-primary to-blue-500 transition-all duration-700"
              style={{
                width: `${(onboarding.completed_count / onboarding.total_count) * 100}%`,
              }}
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {onboarding.steps.map((step) => (
              <Link
                key={step.key}
                href={step.link}
                className={`flex items-start gap-3 p-4 rounded-xl border transition-colors hover:bg-secondary/50 ${
                  step.completed
                    ? "border-primary/20 bg-primary/5"
                    : "border-border"
                }`}
              >
                {step.completed ? (
                  <CheckCircle2 className="size-5 text-primary mt-0.5 shrink-0" />
                ) : (
                  <Circle className="size-5 text-muted-foreground mt-0.5 shrink-0" />
                )}
                <div className="flex-1 min-w-0">
                  <div className={`text-sm font-medium ${step.completed ? "text-primary" : ""}`}>
                    {step.label}
                  </div>
                  <div className="text-xs text-muted-foreground mt-0.5">{step.description}</div>
                </div>
                {!step.completed && <ArrowRight className="size-4 text-muted-foreground mt-0.5 shrink-0" />}
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Stats Grid */}
      {statsLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 animate-pulse py-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="bg-card border border-border rounded-2xl p-5 h-[118px]">
              <div className="flex items-center gap-3">
                <div className="size-11 rounded-xl bg-secondary" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 w-24 bg-secondary rounded" />
                  <div className="h-7 w-16 bg-secondary rounded" />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : statsError ? (
        <div className="flex items-center gap-2 text-red-500 py-4">
          <AlertCircle className="size-4" /> {statsError}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <StatCard
            title="Total Bookings"
            value={stats?.total_bookings ?? 0}
            icon={CalendarCheck}
            chipClass={indigoIcon}
            href="/dashboard/bookings"
            spark={(stats?.bookings_by_day ?? []).map((d) => d.count)}
            sparkColor={chartColors.indigo}
            trend={bookingsTrend}
            badge={stats?.bookings_this_week ? `${stats.bookings_this_week} this week` : null}
          />
          <StatCard
            title="Confirmed"
            value={stats?.confirmed_sessions ?? 0}
            icon={CheckCircle2}
            chipClass={confirmedIcon}
            href="/dashboard/bookings?status=CONFIRMED"
            spark={(stats?.bookings_by_day ?? []).map((d) => d.count)}
            sparkColor={chartColors.green}
            trend={bookingsTrend}
          />
          <StatCard
            title="Cancelled"
            value={stats?.cancelled_sessions ?? 0}
            icon={CalendarX2}
            chipClass={cancelledIcon}
            href="/dashboard/bookings/cancelled"
            spark={(stats?.cancelled_by_day ?? []).map((d) => d.count)}
            sparkColor={chartColors.red}
            trend={cancelledTrend}
          />
          <StatCard
            title="Active Conversations"
            value={stats?.active_sessions ?? 0}
            icon={MessageSquare}
            chipClass={activeIcon}
            href="/dashboard/conversations"
            spark={[]}
            sparkColor={chartColors.blue}
            trend={null}
            badge={stats?.has_schedule ? null : "Schedule needed"}
          />
        </div>
      )}

      {/* Area chart + donut */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6 lg:col-span-2">
          <CardHeader
            icon={Activity}
            title="Activity Overview"
            subtitle={`Bookings vs cancellations — ${range}-day window`}
            iconClass={indigoIcon}
            action={
              <select
                value={range}
                onChange={(e) => {
                  setRange(Number(e.target.value));
                }}
                className="rounded-md border border-border bg-background px-2 py-1 text-xs text-muted-foreground outline-none focus:border-primary"
              >
                {WINDOW_SIZES.map((d) => (
                  <option key={d} value={d}>
                    {d} days
                  </option>
                ))}
              </select>
            }
          />
          {statsLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground text-sm h-56">
              <Loader2 className="size-4 animate-spin" /> Loading chart...
            </div>
          ) : statsError ? (
            <div className="flex items-center gap-2 text-red-500 text-sm h-56">
              <AlertCircle className="size-4" /> {statsError}
            </div>
          ) : areaData.length === 0 || !hasActivity ? (
            <div className="text-center py-16">
              <Activity className="size-8 text-muted-foreground/40 mx-auto mb-2" />
              <p className="text-muted-foreground text-sm">No booking activity yet.</p>
            </div>
          ) : (
            <div
              ref={chartScrollRef}
              className="h-64 overflow-x-auto overflow-y-hidden scrollbar-thin"
            >
              <div
                className="h-full"
                style={{ width: `${(areaData.length / 7) * 100}%`, minWidth: "100%" }}
              >
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={areaData} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                  <defs>
                    <linearGradient id="gradBookings" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={chartColors.indigo} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={chartColors.indigo} stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="gradCancelled" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={chartColors.cancelled} stopOpacity={0.3} />
                      <stop offset="100%" stopColor={chartColors.cancelled} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke={chartColors.grid} strokeDasharray="3 3" vertical={false} />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 11, fill: chartColors.tick }}
                    tickLine={false}
                    axisLine={{ stroke: chartColors.grid }}
                    tickFormatter={(v: string) => {
                      const d = new Date(`${v}T00:00:00`);
                      return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
                    }}
                    minTickGap={24}
                  />
                  <YAxis
                    tick={{ fontSize: 11, fill: chartColors.tick }}
                    tickLine={false}
                    axisLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip {...chartTooltip} />
                  <Area
                    type="monotone"
                    dataKey="Bookings"
                    stroke={chartColors.indigo}
                    strokeWidth={2.5}
                    fill="url(#gradBookings)"
                  />
                  <Area
                    type="monotone"
                    dataKey="Cancelled"
                    stroke={chartColors.cancelled}
                    strokeWidth={2}
                    fill="url(#gradCancelled)"
                  />
                </AreaChart>
              </ResponsiveContainer>
              </div>
            </div>
          )}
        </div>

        {/* Bookings vs Cancelled */}
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6">
          <CardHeader
            icon={CalendarX2}
            title="Bookings vs Cancelled"
            subtitle="Lifetime split"
            iconClass={cardIcon}
          />
          {statsLoading ? (
            <div className="flex items-center justify-center text-muted-foreground text-sm h-48">
              <Loader2 className="size-4 animate-spin" />
            </div>
          ) : statsError ? (
            <div className="flex items-center gap-2 text-red-500 text-sm py-8">
              <AlertCircle className="size-4" /> {statsError}
            </div>
          ) : hasActivity ? (
            <DonutChart booked={stats?.total_bookings ?? 0} cancelled={stats?.cancelled_sessions ?? 0} />
          ) : (
            <div className="text-center py-12">
              <CalendarX2 className="size-8 text-muted-foreground/40 mx-auto mb-2" />
              <p className="text-muted-foreground text-sm">No booking activity yet.</p>
            </div>
          )}
        </div>
      </div>


      {/* Row 1: Bookings by Channel + WhatsApp Stats */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6">
          <CardHeader
            icon={BarChart3}
            title="Bookings by Channel"
            subtitle="Confirmed sessions"
            iconClass={activeIcon}
          />
          {statsLoading ? (
            <div className="flex items-center justify-center text-muted-foreground text-sm h-56">
              <Loader2 className="size-4 animate-spin" />
            </div>
          ) : statsError ? (
            <div className="flex items-center gap-2 text-red-500 text-sm py-8">
              <AlertCircle className="size-4" /> {statsError}
            </div>
          ) : barData.length === 0 ? (
            <div className="text-center py-12">
              <BarChart3 className="size-8 text-muted-foreground/40 mx-auto mb-2" />
              <p className="text-muted-foreground text-sm">No channel data yet.</p>
            </div>
          ) : (
            <ChannelBreakdown data={barData} colors={barColors} />
          )}
        </div>
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6">
          <CardHeader
            icon={MessageSquare}
            title="WhatsApp Stats"
            subtitle="Last 7 days performance"
            iconClass="bg-green-500/15 text-green-500"
          />
          {statsLoading ? (
            <div className="flex items-center justify-center text-muted-foreground text-sm h-48">
              <Loader2 className="size-4 animate-spin" />
            </div>
          ) : statsError ? (
            <div className="flex items-center gap-2 text-red-500 text-sm py-8">
              <AlertCircle className="size-4" /> {statsError}
            </div>
          ) : hasActivity ? (
            <>
              <MiniTrendArea
                data={stats?.whatsapp_bookings_by_day?.slice(-7).map((d) => ({
                  date: d.date,
                  count: d.count,
                })) ?? []}
                color={chartColors.green}
                tooltipBg={chartColors.tooltipBg}
                tooltipBorder={chartColors.tooltipBorder}
                tickColor={chartColors.tick}
              />
              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between gap-4 rounded-lg px-2 py-1.5 hover:bg-secondary/30 transition-colors">
                  <div className="flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-green-500" />
                    <span className="text-sm text-muted-foreground">Bookings today</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline justify-end gap-2">
                      <span className="text-xl font-bold">
                        {stats?.whatsapp_bookings_by_day?.[stats.whatsapp_bookings_by_day.length - 1]?.count ?? 0}
                      </span>
                    </div>
                    <StatMiniBar
                      value={stats?.whatsapp_bookings_by_day?.[stats.whatsapp_bookings_by_day.length - 1]?.count ?? 0}
                      max={Math.max(...(stats?.whatsapp_bookings_by_day?.slice(-7).map((d) => d.count) ?? [0]))}
                      color={chartColors.green}
                    />
                  </div>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-lg px-2 py-1.5 hover:bg-secondary/30 transition-colors">
                  <div className="flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-emerald-500" />
                    <span className="text-sm text-muted-foreground">Avg per day</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline justify-end gap-2">
                      <span className="text-xl font-bold">
                        {stats?.whatsapp_bookings_by_day
                          ? Math.round(stats.whatsapp_bookings_by_day.slice(-7).reduce((s, d) => s + d.count, 0) / 7)
                          : 0}
                      </span>
                    </div>
                    <StatMiniBar
                      value={stats?.whatsapp_bookings_by_day ? Math.round(stats.whatsapp_bookings_by_day.slice(-7).reduce((s, d) => s + d.count, 0) / 7) : 0}
                      max={Math.max(...(stats?.whatsapp_bookings_by_day?.slice(-7).map((d) => d.count) ?? [0]))}
                      color={chartColors.green}
                    />
                  </div>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-lg px-2 py-1.5 hover:bg-secondary/30 transition-colors">
                  <div className="flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-teal-500" />
                    <span className="text-sm text-muted-foreground">WhatsApp this week</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline justify-end gap-2">
                      <span className="text-xl font-bold">
                        {stats?.whatsapp_bookings_by_day
                          ? stats.whatsapp_bookings_by_day.slice(-7).reduce((s, d) => s + d.count, 0)
                          : 0}
                      </span>
                    </div>
                    <StatMiniBar
                      value={stats?.whatsapp_bookings_by_day ? stats.whatsapp_bookings_by_day.slice(-7).reduce((s, d) => s + d.count, 0) : 0}
                      max={Math.max(...(stats?.whatsapp_bookings_by_day?.slice(-7).map((d) => d.count) ?? [0]))}
                      color={chartColors.green}
                    />
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="text-center py-10">
              <MessageSquare className="size-8 text-muted-foreground/40 mx-auto mb-2" />
              <p className="text-muted-foreground text-sm">No WhatsApp activity yet.</p>
            </div>
          )}
        </div>
      </div>

      {/* Row 2: Quick Stats + Recent Appointments */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4 items-stretch">
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6 flex flex-col">
          <CardHeader
            icon={TrendingUp}
            title="Quick Stats"
            subtitle="Last 7 days performance"
            iconClass={confirmedIcon}
          />
          <div className="flex-1 flex flex-col justify-center">
          {statsLoading ? (
            <div className="flex items-center justify-center text-muted-foreground text-sm h-48">
              <Loader2 className="size-4 animate-spin" />
            </div>
          ) : statsError ? (
            <div className="flex items-center gap-2 text-red-500 text-sm py-8">
              <AlertCircle className="size-4" /> {statsError}
            </div>
          ) : hasActivity ? (
            <>
              <MiniTrendArea
                data={stats?.bookings_by_day?.slice(-7).map((d) => ({
                  date: d.date,
                  count: d.count,
                })) ?? []}
                color={chartColors.indigo}
                tooltipBg={chartColors.tooltipBg}
                tooltipBorder={chartColors.tooltipBorder}
                tickColor={chartColors.tick}
              />
              <div className="mt-4 space-y-3">
                <div className="flex items-center justify-between gap-4 rounded-lg px-2 py-1.5 hover:bg-secondary/30 transition-colors">
                  <div className="flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-indigo-500" />
                    <span className="text-sm text-muted-foreground">Bookings today</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline justify-end gap-2">
                      <span className="text-xl font-bold">
                        {stats?.bookings_by_day?.[stats.bookings_by_day.length - 1]?.count ?? 0}
                      </span>
                    </div>
                    <StatMiniBar
                      value={stats?.bookings_by_day?.[stats.bookings_by_day.length - 1]?.count ?? 0}
                      max={Math.max(...(stats?.bookings_by_day?.slice(-7).map((d) => d.count) ?? [0]))}
                      color={chartColors.indigo}
                    />
                  </div>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-lg px-2 py-1.5 hover:bg-secondary/30 transition-colors">
                  <div className="flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-green-500" />
                    <span className="text-sm text-muted-foreground">Avg per day</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline justify-end gap-2">
                      <span className="text-xl font-bold">
                        {stats?.bookings_by_day
                          ? Math.round(stats.bookings_by_day.slice(-7).reduce((s, d) => s + d.count, 0) / 7)
                          : 0}
                      </span>
                    </div>
                    <StatMiniBar
                      value={stats?.bookings_by_day ? Math.round(stats.bookings_by_day.slice(-7).reduce((s, d) => s + d.count, 0) / 7) : 0}
                      max={Math.max(...(stats?.bookings_by_day?.slice(-7).map((d) => d.count) ?? [0]))}
                      color={chartColors.indigo}
                    />
                  </div>
                </div>
                <div className="flex items-center justify-between gap-4 rounded-lg px-2 py-1.5 hover:bg-secondary/30 transition-colors">
                  <div className="flex items-center gap-2">
                    <span className="size-1.5 rounded-full bg-blue-500" />
                    <span className="text-sm text-muted-foreground">This week</span>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-baseline justify-end gap-2">
                      <span className="text-xl font-bold">
                        {stats?.bookings_this_week ?? 0}
                      </span>
                    </div>
                    <StatMiniBar
                      value={stats?.bookings_this_week ?? 0}
                      max={Math.max(...(stats?.bookings_by_day?.slice(-7).map((d) => d.count) ?? [0]))}
                      color={chartColors.blue}
                    />
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="text-center py-10">
              <TrendingUp className="size-8 text-muted-foreground/40 mx-auto mb-2" />
              <p className="text-muted-foreground text-sm">No booking activity yet.</p>
            </div>
          )}
          </div>
        </div>
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6 flex flex-col">
          <div className="flex items-center justify-between mb-4">
            <CardHeader
              icon={CalendarCheck}
              title="Recent Appointments"
              subtitle="Latest confirmed bookings"
              iconClass={confirmedIcon}
            />
<Link
              href="/dashboard/bookings?status=CONFIRMED"
              className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              View all <ArrowRight className="size-3.5" />
            </Link>
          </div>
          {bookingsLoading ? (
            <div className="space-y-2 animate-pulse">
              {[...Array(5)].map((_, i) => (
                <div key={i} className="flex items-center gap-3 p-3 border border-border rounded-lg bg-background/50">
                  <div className="size-9 bg-secondary rounded-full" />
                  <div className="flex-1 space-y-2">
                    <div className="h-3.5 w-28 bg-secondary rounded" />
                    <div className="h-3 w-40 bg-secondary rounded" />
                  </div>
                  <div className="h-3 w-16 bg-secondary rounded" />
                </div>
              ))}
            </div>
          ) : bookingsError ? (
            <div className="flex items-center gap-2 text-red-500 py-4">
              <AlertCircle className="size-4" /> {bookingsError}
            </div>
          ) : !bookings || !Array.isArray(bookings) || bookings.length === 0 ? (
            <Empty className="flex-1 border border-dashed rounded-xl">
              <EmptyHeader>
                <EmptyMedia variant="icon">
                  <CalendarCheck />
                </EmptyMedia>
                <EmptyTitle>No bookings yet</EmptyTitle>
                <EmptyDescription>
                  Deploy your widget and get your first booking to see it here.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          ) : (
            <div className="flex-1 min-h-0 overflow-y-auto pr-1 scrollbar-thin max-h-[340px]">
              <div className="space-y-2">
                {bookings.map((b) => (
                  <div key={b.id} className="group flex items-center gap-3 p-3 border border-border rounded-lg bg-background/50 hover:bg-secondary/30 transition-colors">
                    <HoverCard>
                      <HoverCardTrigger render={<div className="cursor-default" />}>
                        <AvatarInitials name={b.customer_name} tone={nameTone(b.customer_name)} />
                      </HoverCardTrigger>
                      <HoverCardContent className="w-60" align="start">
                        <div className="flex items-center gap-3">
                          <Avatar className="size-9">
                            <AvatarFallback className="text-xs font-semibold">
                              {getInitials(b.customer_name)}
                            </AvatarFallback>
                          </Avatar>
                          <div className="min-w-0">
                            <div className="text-sm font-semibold truncate">{b.customer_name}</div>
                            <div className="text-xs text-muted-foreground truncate">{b.customer_email}</div>
                          </div>
                        </div>
                        {b.customer_phone && (
                          <div className="mt-2 text-xs text-muted-foreground border-t border-border pt-2">
                            {b.customer_phone}
                          </div>
                        )}
                      </HoverCardContent>
                    </HoverCard>
                    <div className="flex-1 min-w-0">
                      <div className="font-medium text-[13px] truncate group-hover:text-primary transition-colors">{b.customer_name}</div>
                      <div className="text-[11px] text-muted-foreground truncate">{b.customer_email}</div>
                    </div>
                    <div className="text-right shrink-0">
                      <div className="text-[11px] text-muted-foreground">
                        {formatDateTime(b.slot_start)}
                      </div>
                    </div>
                    <DropdownMenu>
                      <DropdownMenuTrigger render={<button
                        className="inline-flex items-center justify-center size-7 rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer shrink-0"
                        aria-label={`Actions for ${b.customer_name}`}
                      />}>
                        <MoreHorizontal className="size-4" />
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-44">
                        <DropdownMenuItem
                          onClick={() => {
                            navigator.clipboard.writeText(b.customer_email);
                            toast.success("Email copied");
                          }}
                        >
                          <Mail className="size-3.5" /> Copy email
                        </DropdownMenuItem>
                        {b.customer_phone ? (
                          <DropdownMenuItem
                            onClick={() => {
                              const phone = b.customer_phone ?? "";
                              navigator.clipboard.writeText(phone);
                              toast.success("Phone copied");
                            }}
                          >
                            <Phone className="size-3.5" /> Copy phone
                          </DropdownMenuItem>
                        ) : null}
                        <DropdownMenuSeparator />
                        <DropdownMenuItem render={<Link href="/dashboard/bookings?status=CONFIRMED" className="w-full" />}>
                          <Copy className="size-3.5" /> View in bookings
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}