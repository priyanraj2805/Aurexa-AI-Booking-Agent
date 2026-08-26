"use client";

import type { ReactNode } from "react";

// Booking session status pill with leading dot (TradeBotX badge anatomy).
type Tone = {
  label?: string;
  className: string;
  dot: string;
};

const STATUS_TONES: Record<string, Tone> = {
  CONFIRMED: {
    className: "bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/20",
    dot: "bg-green-500",
  },
  CANCELLED: {
    className: "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20",
    dot: "bg-red-500",
  },
  ACTIVE: {
    className: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
    dot: "bg-blue-500",
  },
  CONFIRMING: {
    className: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20",
    dot: "bg-amber-500",
  },
  EXPIRED: {
    className: "bg-zinc-500/10 text-muted-foreground border-zinc-500/20",
    dot: "bg-zinc-400",
  },
};

const FALLBACK: Tone = {
  className: "bg-secondary/60 text-muted-foreground border-border",
  dot: "bg-muted-foreground/50",
};

export function StatusBadge({
  status,
  children,
}: {
  status?: string | null;
  /** Override pill text (e.g. fixed label for the cancelled-bookings table). */
  children?: ReactNode;
}) {
  const key = (status ?? "").toUpperCase();
  const tone = STATUS_TONES[key] ?? FALLBACK;
  const label =
    children ??
    tone.label ??
    (key ? status!.charAt(0) + status!.slice(1).toLowerCase() : "—");
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-[10px] font-semibold px-2 py-1 rounded-full border ${tone.className}`}
    >
      <span className={`size-1.5 rounded-full ${tone.dot}`} />
      {label}
    </span>
  );
}

/** Theme-aware avatar tile for person rows (replaces hardcoded dark circles). */
export function AvatarInitials({
  name,
  tone = "primary",
}: {
  name: string;
  tone?: "primary" | "red";
}) {
  const initials = name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .toUpperCase()
    .slice(0, 2);
  const cls =
    tone === "red"
      ? "from-red-500/25 to-red-500/5 text-red-600 dark:text-red-400"
      : "from-primary/25 to-primary/5 text-primary";
  return (
    <span
      className={`flex items-center justify-center size-9 rounded-full bg-gradient-to-br shrink-0 text-xs font-bold ring-1 ring-border/60 ${cls}`}
    >
      {initials}
    </span>
  );
}
