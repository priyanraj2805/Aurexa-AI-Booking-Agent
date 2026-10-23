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
  ChevronRight,
  X,
  Circle,
  TrendingUp,
  TrendingDown,
  Activity,
  Target,
} from "lucide-react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { useAuth } from "@/lib/auth-context";
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
} from "recharts";
import {
  useStats,
  useRecentBookings,
  useOnboardingStatus,
  useRevenue,
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

/** Animates from the previous value to `target` with an ease-out count-up. */
function useCountUp(target: number, duration = 900): number {
  const [display, setDisplay] = useState(0);
  const prevRef = useRef(0);
  useEffect(() => {
    const from = prevRef.current;
    if (from === target) return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min((now - start) / duration, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setDisplay(Math.round(from + (target - from) * eased));
      if (t < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        prevRef.current = target;
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return display;
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
          <XAxis dataKey="date" hide />
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
              return isNaN(d.getTime()) ? label : d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
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
  const shown = useCountUp(value);
  return (
    <Link
      href={href}
      className="group relative overflow-hidden rounded-2xl border border-border bg-card p-5 transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/40 hover:shadow-lg hover:shadow-black/5 dark:hover:shadow-black/40"
    >
      <div className="absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-transparent via-primary/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
      <div className="flex flex-wrap items-center gap-3 gap-y-1">
        <span className={`flex items-center justify-center size-9 sm:size-11 rounded-xl shrink-0 ${chipClass}`}>
          <Icon className="size-4 sm:size-5" />
        </span>
        <div className="flex-1 min-w-0 overflow-hidden">
          <div className="text-[13px] font-medium text-muted-foreground truncate">{title}</div>
          <div className="flex flex-wrap items-baseline gap-1.5">
            <span className="text-[26px] lg:text-[28px] font-bold tracking-tight leading-none tabular-nums whitespace-nowrap">{shown}</span>
            {trend !== null && trend !== 0 && (
              <span
                className={`inline-flex items-center gap-0.5 text-[11px] font-semibold whitespace-nowrap ${
                  trend > 0 ? "text-green-500" : "text-red-500"
                }`}
              >
                {trend > 0 ? <TrendingUp className="size-3 shrink-0" /> : <TrendingDown className="size-3 shrink-0" />}
                {trend > 0 ? "+" : ""}
                {trend}%
              </span>
            )}
          </div>
          {badge && <div className="text-[11px] text-amber-500 mt-0.5 truncate">{badge}</div>}
        </div>
        <span className="hidden sm:inline-flex shrink-0"><Sparkline data={spark} color={sparkColor} /></span>
      </div>
    </Link>
  );
}

// Revenue: interactive breakdown per department and team member, mirroring
// the Departments/Team cards — tap a department to see its members and
// their revenue. Org-only (individual orgs have no departments/team);
// staff logins see their own scoped numbers.
const REVENUE_RANGES = [7, 30, 90];

function inrFromPaise(paise: number) {
  return `₹${(paise / 100).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

function RevenueStrip() {
  const { user, isDoctor } = useAuth();
  const isOrg = (user as unknown as { org_type?: string } | null)?.org_type === "organization";
  const [range, setRange] = useState(30);
  const [selectedUnit, setSelectedUnit] = useState<{ id: string; name: string } | null>(null);
  const [allUnits, setAllUnits] = useState<{ id: string; name: string }[]>([]);
  const [allProviders, setAllProviders] = useState<{ id: string; full_name: string; role_label?: string | null; units: string[] }[]>([]);
  // Team-member revenue is scoped to the tapped department's bookings.
  const { data, loading } = useRevenue(range, isOrg && !isDoctor, selectedUnit?.id);

  // Full catalog so zero-revenue departments/members still show — the same
  // orgApi pattern as the Departments/Team cards.
  useEffect(() => {
    if (!isOrg || isDoctor) return;
    import("@/lib/org-api").then(({ orgApi }) =>
      Promise.all([orgApi.units().catch(() => []), orgApi.providers().catch(() => [])]).then(([u, p]) => {
        setAllUnits((u as { id: string; name: string }[]).map((x) => ({ id: x.id, name: x.name })));
        setAllProviders(
          (p as { id: string; full_name: string; role_label?: string | null; units: string[] }[]).map((x) => ({
            id: x.id,
            full_name: x.full_name,
            role_label: x.role_label,
            units: x.units || [],
          }))
        );
      })
    ).catch(() => {});
  }, [isOrg, isDoctor]);

  if (!isOrg || isDoctor) return null;

  const totals = data?.totals;
  const deptRev = new Map((data?.by_department ?? []).map((d) => [d.id ?? "", d]));
  const memberRev = new Map((data?.by_member ?? []).map((m) => [m.id ?? "", m]));
  const unassignedMember = (data?.by_member ?? []).find((m) => !m.id && m.bookings > 0) ?? null;
  const hasBookings = (totals?.bookings ?? 0) > 0;

  // Department rows: full catalog, else only departments with bookings.
  const deptRows = (allUnits.length
    ? allUnits.map((u) => ({ id: u.id, name: u.name }))
    : (data?.by_department ?? []).filter((d) => d.id).map((d) => ({ id: d.id as string, name: d.name }))
  )
    .map((u) => {
      const r = deptRev.get(u.id);
      return {
        ...u,
        bookings: r?.bookings ?? 0,
        gross: r?.gross_revenue_paise ?? 0,
        collected: r?.collected_revenue_paise ?? 0,
      };
    })
    .sort((a, b) => b.gross - a.gross || b.bookings - a.bookings);

  // Team rows: the tapped department's doctors, else everyone.
  const teamPool = selectedUnit
    ? allProviders.filter((p) => p.units.includes(selectedUnit.id))
    : allProviders;
  const useCatalogTeam = selectedUnit ? true : allProviders.length > 0;
  const teamRows = (
    useCatalogTeam
      ? teamPool.map((p) => ({ id: p.id, name: p.full_name, role_label: p.role_label }))
      : (data?.by_member ?? []).map((m) => ({ id: m.id ?? "", name: m.name, role_label: m.role_label }))
  )
    .map((p) => {
      const r = memberRev.get(p.id);
      return {
        ...p,
        bookings: r?.bookings ?? 0,
        gross: r?.gross_revenue_paise ?? 0,
        collected: r?.collected_revenue_paise ?? 0,
      };
    })
    .sort((a, b) => b.gross - a.gross || b.bookings - a.bookings);

  // Summary chips follow the selection: the tapped department's numbers
  // when filtered, org-wide totals otherwise.
  const sel = selectedUnit ? deptRev.get(selectedUnit.id) ?? null : null;
  const chips = sel
    ? { bookings: sel.bookings, gross: sel.gross_revenue_paise, collected: sel.collected_revenue_paise }
    : totals
      ? { bookings: totals.bookings, gross: totals.gross_revenue_paise, collected: totals.collected_revenue_paise }
      : null;

  const maxDept = Math.max(1, ...deptRows.map((d) => d.gross));
  const maxTeam = Math.max(1, ...teamRows.map((t) => t.gross));
  const onRange = (v: number) => {
    setRange(v);
    setSelectedUnit(null);
  };

  return (
    <div className="mt-4 rounded-2xl border border-border bg-card p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold">
            Revenue{selectedUnit ? ` — ${selectedUnit.name}` : ""}
          </h3>
          <select
            value={range}
            onChange={(e) => onRange(Number(e.target.value))}
            className="rounded-md border border-border bg-background px-2 py-1 text-xs text-muted-foreground outline-none focus:border-primary"
            aria-label="Revenue range"
          >
            {REVENUE_RANGES.map((d) => (
              <option key={d} value={d}>Last {d} days</option>
            ))}
          </select>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="rounded-xl border border-border bg-background p-3">
            <div className="text-[11px] text-muted-foreground">Gross revenue</div>
            <div className="text-lg font-semibold tabular-nums">
              {chips ? inrFromPaise(chips.gross) : "—"}
            </div>
          </div>
          <div className="rounded-xl border border-border bg-background p-3">
            <div className="text-[11px] text-muted-foreground">Collected revenue</div>
            <div className="text-lg font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
              {chips ? inrFromPaise(chips.collected) : "—"}
            </div>
          </div>
          <div className="rounded-xl border border-border bg-background p-3">
            <div className="text-[11px] text-muted-foreground">Bookings</div>
            <div className="text-lg font-semibold tabular-nums">{chips?.bookings ?? "—"}</div>
          </div>
        </div>
        <p className="mt-2 text-[11px] text-muted-foreground">
          Gross = booked amount (provider fee / appointment price) of non-cancelled bookings. Collected = payments actually received.
        </p>

      {loading && !data ? (
        <div className="mt-5 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading revenue…
        </div>
      ) : !hasBookings ? (
        <div className="mt-5 text-sm text-muted-foreground">
          No bookings in this period.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-5 pt-5 border-t border-border">
          {/* Departments — own card; tap one to filter the team list */}
          {deptRows.length > 0 && (
            <div className="min-w-0 rounded-xl border border-border bg-background p-4">
              <div className="flex items-center justify-between mb-1">
                <h3 className="text-sm font-semibold">Departments</h3>
                <span className="text-[11px] text-muted-foreground">{deptRows.length} total</span>
              </div>
              <p className="text-xs text-muted-foreground mb-3">
                Tap a department to see its team members.
              </p>
              <div className="space-y-2 max-h-[300px] overflow-y-auto overflow-x-hidden pr-1 scrollbar-thin">
                {deptRows.map((u) => {
                  const active = selectedUnit?.id === u.id;
                  return (
                    <button
                      key={u.id}
                      type="button"
                      onClick={() => setSelectedUnit(active ? null : { id: u.id, name: u.name })}
                      aria-pressed={active}
                      className={`group w-full text-left rounded-xl border px-3 py-2.5 transition-colors ${
                        active
                          ? "border-primary bg-primary/10 ring-1 ring-primary/40"
                          : "border-border bg-card hover:border-primary/50 hover:bg-secondary/40"
                      }`}
                    >
                      <div className="flex items-center gap-2 text-sm">
                        <span className="font-semibold truncate">{u.name}</span>
                        <span className="ml-auto tabular-nums font-semibold shrink-0">{inrFromPaise(u.gross)}</span>
                        {active ? (
                          <span className="shrink-0 text-[10px] font-bold uppercase tracking-wide text-primary">Viewing ✕</span>
                        ) : (
                          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground opacity-50 group-hover:opacity-100 transition-opacity" />
                        )}
                      </div>
                      <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
                        <span>{u.bookings} booking{u.bookings === 1 ? "" : "s"}</span>
                        <span className="text-emerald-600 dark:text-emerald-400">{inrFromPaise(u.collected)} collected</span>
                      </div>
                      <div className="mt-2 h-1.5 rounded-full bg-secondary overflow-hidden">
                        <div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(u.gross > 0 ? 4 : 0, Math.round((u.gross / maxDept) * 100))}%` }} />
                      </div>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Team members — own card; scoped to the tapped department */}
          <div className="min-w-0 rounded-xl border border-border bg-background p-4">
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-sm font-semibold">
                Team members
                {selectedUnit ? <span className="hidden sm:inline text-muted-foreground font-normal"> · {selectedUnit.name}</span> : null}
              </h3>
              {selectedUnit ? (
                <button
                  type="button"
                  onClick={() => setSelectedUnit(null)}
                  className="inline-flex items-center gap-1 text-[11px] font-semibold pl-2.5 pr-2 py-1 rounded-full bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
                  title="Show all team members"
                >
                  <span className="truncate max-w-[140px]">{selectedUnit.name}</span>
                  <X className="h-3 w-3 shrink-0" />
                </button>
              ) : (
                <span className="text-[11px] text-muted-foreground">All departments</span>
              )}
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              {selectedUnit
                ? `Revenue in the last ${range} days — filtered to ${selectedUnit.name}`
                : `Revenue in the last ${range} days — across all departments`}
            </p>
            {teamRows.length || (!selectedUnit && unassignedMember) ? (
              <div className="space-y-2 max-h-[300px] overflow-y-auto overflow-x-hidden pr-1 scrollbar-thin">
                {teamRows.map((p) => (
                  <div key={p.id} className="rounded-xl border border-border bg-card px-3 py-2.5">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-semibold truncate">
                        {p.name}
                        {p.role_label ? (
                          <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">{p.role_label}</span>
                        ) : null}
                      </span>
                      <span className="ml-auto tabular-nums font-semibold shrink-0">{inrFromPaise(p.gross)}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
                      <span>{p.bookings} booking{p.bookings === 1 ? "" : "s"}</span>
                      <span className="text-emerald-600 dark:text-emerald-400">{inrFromPaise(p.collected)} collected</span>
                    </div>
                    <div className="mt-2 h-1.5 rounded-full bg-secondary overflow-hidden">
                      <div className="h-full rounded-full bg-green-500" style={{ width: `${Math.max(p.gross > 0 ? 4 : 0, Math.round((p.gross / maxTeam) * 100))}%` }} />
                    </div>
                  </div>
                ))}
                {!selectedUnit && unassignedMember ? (
                  <div className="rounded-xl border border-dashed border-border px-3 py-2.5">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-semibold truncate text-muted-foreground">Unassigned</span>
                      <span className="ml-auto tabular-nums font-semibold shrink-0">{inrFromPaise(unassignedMember.gross_revenue_paise)}</span>
                    </div>
                    <div className="mt-1 text-[11px] text-muted-foreground">
                      {unassignedMember.bookings} booking{unassignedMember.bookings === 1 ? "" : "s"}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground py-6 text-center rounded-xl border border-dashed border-border">
                {selectedUnit ? "No team members in this department." : "No bookings in this range."}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}


type ChannelScope = "all" | "web" | "whatsapp";

/**
 * One card for everything channel-related: lifetime mix (web vs WhatsApp),
 * a 7-day trend, and today/avg/week for the selected channel. This replaces
 * three separate cards that each repeated the same numbers.
 */
function ChannelPerformance({
  stats,
  loading,
  error,
  chartColors,
}: {
  stats: Stats | null | undefined;
  loading: boolean;
  error: string | null;
  chartColors: {
    blue: string;
    green: string;
    indigo: string;
    tick: string;
    tooltipBg: string;
    tooltipBorder: string;
  };
}) {
  const [scope, setScope] = useState<ChannelScope>("all");

  const web = stats?.channel_breakdown?.find((c) => c.channel === "web")?.count ?? 0;
  const wa = stats?.channel_breakdown?.find((c) => c.channel === "whatsapp")?.count ?? 0;
  const channelTotal = web + wa;

  const series =
    scope === "all"
      ? (stats?.web_bookings_by_day ?? []).map((d, i) => ({
          date: d.date,
          count: d.count + (stats?.whatsapp_bookings_by_day?.[i]?.count ?? 0),
        }))
      : scope === "web"
        ? stats?.web_bookings_by_day ?? []
        : stats?.whatsapp_bookings_by_day ?? [];

  const last7 = series.slice(-7);
  const today = last7.length ? last7[last7.length - 1].count : 0;
  const weekTotal = last7.reduce((s, d) => s + d.count, 0);
  const avg = Math.round(weekTotal / 7);
  const maxVal = Math.max(...last7.map((d) => d.count), 1);
  const scopedTotal = scope === "all" ? channelTotal : scope === "web" ? web : wa;
  const color =
    scope === "web" ? chartColors.blue : scope === "whatsapp" ? chartColors.green : chartColors.indigo;
  const share = (n: number) => (channelTotal > 0 ? Math.round((n / channelTotal) * 100) : 0);

  return (
    <div className="bg-card border border-border rounded-2xl p-4 sm:p-6 flex flex-col transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/5 dark:hover:shadow-black/40">
      <CardHeader
        icon={MessageSquare}
        title="Channel Performance"
        subtitle="Confirmed bookings — 7-day trend + lifetime Bookings"
        iconClass="bg-blue-500/15 text-blue-500"
        action={
          <select
            value={scope}
            onChange={(e) => setScope(e.target.value as ChannelScope)}
            className="rounded-md border border-border bg-background px-2 py-1 text-xs text-muted-foreground outline-none focus:border-primary"
            aria-label="Channel"
          >
            <option value="all">All channels</option>
            <option value="web">Web</option>
            <option value="whatsapp">WhatsApp</option>
          </select>
        }
      />
      {loading ? (
        <div className="flex items-center justify-center text-muted-foreground text-sm flex-1 min-h-[12rem]">
          <Loader2 className="size-4 animate-spin" />
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 text-red-500 text-sm flex-1">
          <AlertCircle className="size-4" /> {error}
        </div>
      ) : channelTotal === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center py-10">
          <MessageSquare className="size-8 text-muted-foreground/40 mx-auto mb-2" />
          <p className="text-muted-foreground text-sm">No confirmed bookings yet.</p>
        </div>
      ) : (
        <>
          {/* Headline: one number, left-aligned, so the card reads top-down */}
          <div className="flex items-baseline gap-2">
            <span className="text-3xl font-bold leading-none tracking-tight tabular-nums">
              {scopedTotal}
            </span>
            <span className="text-xs text-muted-foreground">
              {scope === "all"
                ? "confirmed bookings"
                : `${scope === "web" ? "web" : "WhatsApp"} confirmed`}
            </span>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">Last 7 days</p>
          <div className="mt-2">
            <MiniTrendArea
              data={last7.map((d) => ({ date: d.date, count: d.count }))}
              color={color}
              tooltipBg={chartColors.tooltipBg}
              tooltipBorder={chartColors.tooltipBorder}
              tickColor={chartColors.tick}
            />
          </div>
          {/* Three compact readouts instead of rows-with-bars (bars were noise) */}
          <div className="mt-4 grid grid-cols-3 gap-2">
            {[
              { label: "Today", value: today },
              { label: "Avg / day", value: avg },
              { label: "This week", value: weekTotal },
            ].map((m) => (
              <div
                key={m.label}
                className="rounded-lg border border-border bg-background/60 px-2 py-1.5 text-center"
              >
                <div className="text-lg font-bold leading-tight tabular-nums">{m.value}</div>
                <div className="text-[10px] text-muted-foreground">{m.label}</div>
              </div>
            ))}
          </div>
          {/* Fills leftover height when this card is the shorter one in the
              row, so the lifetime block stays pinned to the card bottom
              instead of a gap opening up beneath it. */}
          <div className="flex-1" />
          {/* Lifetime mix: label, bar, count · % on one line */}
          <div className="mt-4 pt-3 border-t border-border">
            <div className="flex items-center justify-between text-[11px] text-muted-foreground">
              <span>Lifetime Bookings</span>
              <span>{channelTotal} confirmed</span>
            </div>
            <div className="mt-2 space-y-2">
              {[
                { label: "Web", value: web, color: chartColors.blue },
                { label: "WhatsApp", value: wa, color: chartColors.green },
              ].map((ch) => (
                <div key={ch.label} className="flex items-center gap-2">
                  <span className="w-16 shrink-0 text-xs text-muted-foreground">{ch.label}</span>
                  <div className="h-1.5 flex-1 rounded-full bg-secondary overflow-hidden">
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${share(ch.value)}%`, backgroundColor: ch.color }}
                    />
                  </div>
                  <span className="w-20 shrink-0 text-right text-xs tabular-nums font-semibold">
                    {ch.value} · {share(ch.value)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Conversations by where they end up. Active / Confirmed / Cancelled /
 * Expired already existed in the database but the dashboard only ever showed
 * the positives — this makes the drop-off (mostly expired chats) visible.
 */
function ConversationFunnel({
  stats,
  loading,
  error,
}: {
  stats: Stats | null | undefined;
  loading: boolean;
  error: string | null;
}) {
  const counts = stats?.conversation_status ?? {};
  const active = counts.ACTIVE ?? 0;
  const confirmed = counts.CONFIRMED ?? 0;
  const cancelled = counts.CANCELLED ?? 0;
  const expired = counts.EXPIRED ?? 0;
  const total = active + confirmed + cancelled + expired;
  const closed = confirmed + cancelled + expired;
  const conversion = closed > 0 ? Math.round((confirmed / closed) * 100) : 0;

  const stages = [
    { label: "Confirmed", value: confirmed, color: "#10b981", hint: "reached a booking" },
    { label: "Expired", value: expired, color: "#f59e0b", hint: "went quiet" },
    { label: "Cancelled", value: cancelled, color: "#ef4444", hint: "dropped off" },
    { label: "Active", value: active, color: "#3b82f6", hint: "in progress" },
  ];

  return (
    <div className="bg-card border border-border rounded-2xl p-4 sm:p-6 flex flex-col transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/5 dark:hover:shadow-black/40">
      <CardHeader
        icon={Target}
        title="Conversation Funnel"
        iconClass="bg-amber-500/15 text-amber-500"
      />
      {loading ? (
        <div className="flex items-center justify-center text-muted-foreground text-sm flex-1 min-h-[10rem]">
          <Loader2 className="size-4 animate-spin" />
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 text-red-500 text-sm flex-1">
          <AlertCircle className="size-4" /> {error}
        </div>
      ) : total === 0 ? (
        <div className="flex-1 flex flex-col items-center justify-center py-10">
          <Target className="size-8 text-muted-foreground/40 mx-auto mb-2" />
          <p className="text-muted-foreground text-sm">No conversations yet.</p>
        </div>
      ) : (
        <>
          <div className="flex items-end justify-between mb-3">
            <div>
              <div className="text-3xl font-bold tracking-tight tabular-nums">{conversion}%</div>
              <div className="text-[11px] text-muted-foreground">chats that became a booking</div>
            </div>
            <div className="text-right">
              <div className="text-lg font-semibold tabular-nums">{total}</div>
              <div className="text-[11px] text-muted-foreground">conversations</div>
            </div>
          </div>
          <div className="flex h-2.5 rounded-full overflow-hidden bg-secondary mb-4">
            {stages.map(
              (s) =>
                s.value > 0 && (
                  <div
                    key={s.label}
                    style={{ width: `${(s.value / total) * 100}%`, backgroundColor: s.color }}
                  />
                )
            )}
          </div>
          {/* Stages absorb the extra height (rows spread evenly) so the funnel
              reaches the same bottom edge as Recent Appointments instead of
              ending short with dead space above the footnote. */}
          <div className="space-y-2 flex-1 flex flex-col justify-between">
            {stages.map((s) => (
              <div key={s.label} className="flex items-center gap-2 text-sm">
                <span className="size-2 rounded-sm shrink-0" style={{ backgroundColor: s.color }} />
                <span className="font-medium">{s.label}</span>
                <span className="text-[11px] text-muted-foreground truncate">{s.hint}</span>
                <span className="ml-auto tabular-nums font-semibold">{s.value}</span>
                <span className="w-9 text-right text-[11px] text-muted-foreground">
                  {total ? Math.round((s.value / total) * 100) : 0}%
                </span>
              </div>
            ))}
          </div>
          <p className="mt-3 pt-3 border-t border-border text-[11px] text-muted-foreground">
            Conversion counts closed chats only — active conversations are excluded.
          </p>
        </>
      )}
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

export default function DashboardPage() {
  const { resolvedTheme } = useTheme();
  const { user, isDoctor } = useAuth();
  const isOrg = (user as unknown as { org_type?: string } | null)?.org_type === "organization";
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
            <span
              className={[
                "text-[11px] font-bold uppercase tracking-wider px-2 py-1 rounded-full",
                isOrg ? "bg-primary/15 text-primary" : "bg-secondary text-muted-foreground",
              ].join(" ")}
            >
              {isDoctor ? "👤 My bookings" : isOrg ? "🏥 Organization" : "🧍 Individual"}
            </span>
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

      {isOrg && <RevenueStrip />}

      {/* Activity overview + channel performance */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mt-4">
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6 flex flex-col lg:col-span-2 transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/5 dark:hover:shadow-black/40">
          <CardHeader
            icon={Activity}
            title="Activity Overview"
            subtitle={`Bookings vs cancellations — ${range}-day window`}
            iconClass={indigoIcon}
            action={
              <div className="flex items-center gap-3">
                <span className="hidden sm:flex items-center gap-3 text-[11px] text-muted-foreground">
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-2 rounded-full" style={{ backgroundColor: chartColors.indigo }} />
                    Bookings
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="size-2 rounded-full" style={{ backgroundColor: chartColors.cancelled }} />
                    Cancelled
                  </span>
                </span>
                <select
                  value={range}
                  onChange={(e) => {
                    setRange(Number(e.target.value));
                  }}
                  className="rounded-md border border-border bg-background px-2 py-1 text-xs text-muted-foreground outline-none focus:border-primary"
                  aria-label="Chart range"
                >
                  {WINDOW_SIZES.map((d) => (
                    <option key={d} value={d}>
                      {d} days
                    </option>
                  ))}
                </select>
              </div>
            }
          />
          {/* Every state takes the remaining card height (min keeps the chart
              readable) so the card bottoms line up with Channel Performance
              without leaving dead space below the chart. */}
          {statsLoading ? (
            <div className="flex items-center gap-2 text-muted-foreground text-sm flex-1 min-h-[14rem]">
              <Loader2 className="size-4 animate-spin" /> Loading chart...
            </div>
          ) : statsError ? (
            <div className="flex items-center gap-2 text-red-500 text-sm flex-1 min-h-[14rem]">
              <AlertCircle className="size-4" /> {statsError}
            </div>
          ) : areaData.length === 0 || !hasActivity ? (
            <div className="flex-1 flex flex-col items-center justify-center py-16">
              <Activity className="size-8 text-muted-foreground/40 mx-auto mb-2" />
              <p className="text-muted-foreground text-sm">No booking activity yet.</p>
            </div>
          ) : (
            <div className="flex-1 min-h-[14rem]">
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
          )}
        </div>

        <ChannelPerformance
          stats={stats}
          loading={statsLoading}
          error={statsError}
          chartColors={chartColors}
        />
      </div>


      {/* Row 2: Conversation funnel + Recent Appointments */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mt-4">
        <ConversationFunnel
          stats={stats}
          loading={statsLoading}
          error={statsError}
        />
        <div className="bg-card border border-border rounded-2xl p-4 sm:p-6 flex flex-col transition-all duration-200 hover:-translate-y-0.5 hover:shadow-lg hover:shadow-black/5 dark:hover:shadow-black/40">
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
                      <div className="flex items-center gap-1.5">
                        <span className="font-medium text-[13px] truncate group-hover:text-primary transition-colors">{b.customer_name}</span>
                        <span className="hidden sm:inline-flex items-center gap-1 rounded-full bg-green-500/10 px-1.5 py-px text-[10px] font-semibold text-green-600 dark:text-green-400 shrink-0">
                          <span className="size-1 rounded-full bg-green-500" />
                          Confirmed
                        </span>
                      </div>
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
