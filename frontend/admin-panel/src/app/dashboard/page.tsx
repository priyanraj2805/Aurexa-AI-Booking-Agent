"use client";

import {
  CalendarCheck,
  CheckCircle2,
  MessageSquare,
  Clock,
  Loader2,
  AlertCircle,
  Rocket,
  ArrowRight,
  Circle,
  Database,
  Mail,
  Bot,
  RefreshCw,
} from "lucide-react";
import Link from "next/link";
import {
  useStats,
  useRecentBookings,
  useOnboardingStatus,
  useDependencyHealth,
  type DependencyHealthCheck,
} from "@/hooks/use-dashboard";

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

export default function DashboardPage() {
  const { data: stats, loading: statsLoading, error: statsError } = useStats();
  const { data: bookings, loading: bookingsLoading, error: bookingsError } = useRecentBookings(10);
  const { data: onboarding, loading: onboardingLoading } = useOnboardingStatus();
  const {
    data: dependencyHealth,
    loading: dependencyHealthLoading,
    error: dependencyHealthError,
    refetch: refetchDependencyHealth,
  } = useDependencyHealth();

  const statCards = stats
    ? [
        { title: "Total Bookings", value: stats.total_bookings, icon: CalendarCheck },
        { title: "Confirmed", value: stats.confirmed_sessions, icon: CheckCircle2 },
        { title: "Active Conversations", value: stats.active_sessions, icon: MessageSquare },
        {
          title: "Bookings This Week",
          value: stats.bookings_this_week,
          icon: Clock,
          badge: stats.has_schedule ? null : "Schedule needed",
        },
      ]
    : [];

  const showOnboarding = onboarding && onboarding.completed_count < onboarding.total_count;

  const dependencyRows: Array<{
    key: "database" | "llm" | "email";
    label: string;
    icon: typeof Database;
    value: DependencyHealthCheck | null;
  }> = [
    { key: "database", label: "Database", icon: Database, value: dependencyHealth?.checks.database ?? null },
    { key: "llm", label: "LLM", icon: Bot, value: dependencyHealth?.checks.llm ?? null },
    { key: "email", label: "Email", icon: Mail, value: dependencyHealth?.checks.email ?? null },
  ];

  const healthBadgeClass =
    dependencyHealth?.status === "down"
      ? "bg-red-500/10 text-red-500"
      : dependencyHealth?.status === "degraded"
      ? "bg-amber-500/10 text-amber-500"
      : "bg-primary/10 text-primary";

  return (
    <>
      <div className="flex items-end justify-between mb-2">
        <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">Dashboard</h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1">
            Real-time performance of your booking AI across all channels.
          </p>
        </div>
      </div>

      {/* Onboarding Checklist */}
      {!onboardingLoading && showOnboarding && (
        <div className="bg-card border border-border rounded-xl p-4 sm:p-6 mb-5">
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

          {/* Progress bar */}
          <div className="w-full h-1.5 bg-secondary rounded-full mb-5">
            <div
              className="h-full bg-primary rounded-full transition-all duration-500"
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
                className={`flex items-start gap-3 p-4 rounded-lg border transition-colors hover:bg-secondary/50 ${
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
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-5 animate-pulse py-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="bg-card border border-border rounded-xl p-6 flex flex-col h-[114px]">
              <div className="flex items-center justify-between mb-4">
                <div className="h-4 w-24 bg-secondary rounded" />
                <div className="size-4 bg-secondary rounded" />
              </div>
              <div className="h-8 w-16 bg-secondary rounded" />
            </div>
          ))}
        </div>
      ) : statsError ? (
        <div className="flex items-center gap-2 text-red-500 py-4">
          <AlertCircle className="size-4" /> {statsError}
        </div>
      ) : (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-5">
          {statCards.map((stat) => (
            <div
              key={stat.title}
              className="bg-card border border-border rounded-xl p-4 sm:p-6 flex flex-col"
            >
              <div className="flex items-center justify-between text-[13px] font-semibold text-muted-foreground mb-3">
                {stat.title}
                <stat.icon className="size-4" />
              </div>
              <div
                className="text-[26px] sm:text-[32px] font-bold tracking-tight leading-none"
                style={{ letterSpacing: "-1px" }}
              >
                {stat.value}
              </div>
              {"badge" in stat && stat.badge && (
                <div className="text-[11px] text-amber-500 mt-2">{stat.badge}</div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="bg-card border border-border rounded-xl p-4 sm:p-5">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div className="flex items-start gap-3">
            <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary">
              <Database className="size-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold">Dependency Health</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                Current service readiness for database, LLM, and email.
              </p>
            </div>
          </div>
          {!dependencyHealthLoading && !dependencyHealthError && dependencyHealth && (
            <span className={`text-[11px] font-semibold uppercase px-2 py-1 rounded ${healthBadgeClass}`}>
              {dependencyHealth.status}
            </span>
          )}
        </div>

        {dependencyHealthLoading ? (
          <div className="flex items-center gap-2 text-muted-foreground text-sm">
            <Loader2 className="size-4 animate-spin" /> Loading dependency health...
          </div>
        ) : dependencyHealthError ? (
          <div className="flex items-center justify-between gap-3 text-red-500 text-sm">
            <span className="flex items-center gap-2">
              <AlertCircle className="size-4" /> {dependencyHealthError}
            </span>
            <button
              className="text-xs text-foreground hover:underline inline-flex items-center gap-1"
              onClick={() => void refetchDependencyHealth()}
            >
              <RefreshCw className="size-3" /> Retry
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            {dependencyRows.map((row) => (
              <div key={row.key} className="flex items-center justify-between gap-3 border border-border rounded-lg px-3 py-2">
                <div className="flex items-center gap-2 text-sm">
                  <row.icon className="size-4 text-muted-foreground" />
                  <span>{row.label}</span>
                </div>
                <div className="flex items-center gap-3 text-xs">
                  <span className="text-muted-foreground">
                    {row.value?.latency_ms != null ? `${row.value.latency_ms} ms` : "—"}
                  </span>
                  <span
                    className={
                      row.value?.status === "down"
                        ? "text-red-500"
                        : row.value?.status === "degraded"
                        ? "text-amber-500"
                        : "text-primary"
                    }
                  >
                    {row.value?.status ?? "unknown"}
                  </span>
                </div>
              </div>
            ))}
            {dependencyHealth?.trace_id && (
              <p className="text-[11px] text-muted-foreground mt-2">
                Trace: <span className="font-mono">{dependencyHealth.trace_id}</span>
              </p>
            )}
          </div>
        )}
      </div>

      {/* Recent Appointments */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-base font-semibold">Recent Appointments</h3>
        </div>

        {bookingsLoading ? (
          <div className="space-y-3 animate-pulse">
            {[...Array(3)].map((_, i) => (
              <div key={i} className="flex items-center justify-between p-4 border border-border rounded-lg bg-background/50">
                <div className="flex items-center gap-4">
                  <div className="size-10 bg-secondary rounded-full" />
                  <div className="space-y-2">
                    <div className="h-4 w-32 bg-secondary rounded" />
                    <div className="h-3 w-48 bg-secondary rounded" />
                  </div>
                </div>
                <div className="h-6 w-20 bg-secondary rounded-full" />
              </div>
            ))}
          </div>
        ) : bookingsError ? (
          <div className="flex items-center gap-2 text-red-500 py-4">
            <AlertCircle className="size-4" /> {bookingsError}
          </div>
        ) : !bookings || !Array.isArray(bookings) || bookings.length === 0 ? (
          <div className="text-center py-10">
            <CalendarCheck className="size-10 text-muted-foreground/40 mx-auto mb-3" />
            <p className="text-muted-foreground text-sm">No bookings yet.</p>
            <p className="text-muted-foreground/60 text-xs mt-1">
              Deploy your widget and get your first booking to see it here.
            </p>
          </div>
        ) : (
          <div className="w-full overflow-x-auto">
            {/* Mobile card view */}
            <div className="md:hidden space-y-3">
              {bookings.map((b) => (
                <div key={b.id} className="flex items-start gap-3 p-4 border border-border rounded-lg bg-background/50">
                  <div className="flex items-center justify-center size-8 rounded-full bg-[#27272a] text-xs font-semibold shrink-0">
                    {getInitials(b.customer_name)}
                  </div>
                  <div className="flex-1 min-w-0 space-y-1">
                    <div className="font-medium text-[13px]">{b.customer_name}</div>
                    <div className="text-[11px] text-muted-foreground truncate">{b.customer_email}</div>
                    {b.customer_phone && (
                      <div className="text-[11px] text-muted-foreground">{b.customer_phone}</div>
                    )}
                    <div className="text-[11px] text-muted-foreground">
                      Meeting: {formatDateTime(b.slot_start)}
                    </div>
                    <div className="text-[11px] text-muted-foreground">
                      Booked: {formatDateTime(b.created_at)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
            {/* Desktop table view */}
            <table className="hidden md:table w-full border-collapse">
              <thead>
                <tr>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Client
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Email
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Meeting Time
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Booked At
                  </th>
                </tr>
              </thead>
              <tbody>
                {bookings.map((b) => (
                  <tr key={b.id} className="hover:bg-secondary/50 transition-colors">
                    <td className="px-4 py-4 text-[13px] border-b border-border">
                      <div className="flex items-center gap-3">
                        <div className="flex items-center justify-center size-8 rounded-full bg-[#27272a] text-xs font-semibold">
                          {getInitials(b.customer_name)}
                        </div>
                        <div>
                          <div className="font-medium">{b.customer_name}</div>
                          {b.customer_phone && (
                            <div className="text-[11px] text-muted-foreground">{b.customer_phone}</div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-4 text-[13px] border-b border-border">{b.customer_email}</td>
                    <td className="px-4 py-4 text-[13px] border-b border-border">
                      {formatDateTime(b.slot_start)}
                      {b.slot_end && <> – {formatDateTime(b.slot_end)}</>}
                    </td>
                    <td className="px-4 py-4 text-[13px] border-b border-border">
                      {formatDateTime(b.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
