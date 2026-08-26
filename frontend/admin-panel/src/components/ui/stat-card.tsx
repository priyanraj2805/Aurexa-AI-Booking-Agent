"use client";

import Link from "next/link";
import { TrendingDown, TrendingUp } from "lucide-react";
import type { LucideIcon } from "lucide-react";

// TradeBotX-style stat card: tinted icon tile, label, big value with trend
// chip, secondary metric line, optional right-side visual (sparkline).
export function StatCard({
  title,
  value,
  icon: Icon,
  iconClassName = "bg-primary/10 text-primary",
  href,
  sub,
  trend,
  trendLabel = "vs previous week",
  right,
}: {
  title: string;
  value: string | number;
  icon: LucideIcon;
  iconClassName?: string;
  href?: string;
  sub?: React.ReactNode;
  trend?: number | null;
  trendLabel?: string;
  right?: React.ReactNode;
}) {
  const body = (
    <>
      <div className="absolute inset-x-0 top-0 h-[3px] bg-gradient-to-r from-transparent via-primary/60 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
      <div className="flex items-center gap-3">
        <span
          className={`flex items-center justify-center size-11 rounded-xl shrink-0 ${iconClassName}`}
        >
          <Icon className="size-5" />
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {title}
          </div>
          <div className="flex items-baseline gap-2 flex-wrap min-w-0 mt-0.5">
            <span className="text-[26px] sm:text-[28px] font-bold tracking-tight leading-none">
              {value}
            </span>
            {trend != null && trend !== 0 && (
              <span
                title={trendLabel}
                className={`inline-flex items-center gap-0.5 text-[11px] font-semibold px-1.5 py-0.5 rounded-full shrink-0 ${
                  trend > 0
                    ? "text-green-600 dark:text-green-400 bg-green-500/10"
                    : "text-red-600 dark:text-red-400 bg-red-500/10"
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
          {sub && (
            <div className="text-[11px] text-muted-foreground mt-1.5 truncate">{sub}</div>
          )}
        </div>
        {right && <div className="shrink-0">{right}</div>}
      </div>
    </>
  );

  const cls =
    "group relative block overflow-hidden rounded-2xl border border-border bg-card p-5 transition-all hover:border-primary/40 hover:shadow-lg hover:shadow-black/5 dark:hover:shadow-black/40";

  if (href) {
    return (
      <Link href={href} className={cls}>
        {body}
      </Link>
    );
  }
  return <div className={cls}>{body}</div>;
}
