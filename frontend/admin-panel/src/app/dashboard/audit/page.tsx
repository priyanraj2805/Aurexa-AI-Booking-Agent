"use client";

import { useMemo, useState } from "react";
import {
  Loader2,
  AlertCircle,
  ScrollText,
  ChevronDown,
  ChevronRight,
  LogIn,
  LogOut,
  XCircle,
  CalendarPlus,
  Pencil,
  Trash2,
  ShieldAlert,
  KeyRound,
  Settings2,
  UserPlus,
  UserX,
  UserCheck,
  UserCog,
  MessageSquare,
  Plus,
  RefreshCw,
  CheckCircle2,
  Building2,
  CreditCard,
  type LucideIcon,
} from "lucide-react";
import { useAuditLogs, type AuditLogItem } from "@/hooks/use-dashboard";
import { SearchInput, DateFilter } from "@/components/filter-bar";
import { Pagination } from "@/components/pagination";

const PAGE_SIZE = 10;

type ActionMeta = {
  verb: string;
  icon: LucideIcon;
  tone: string;
};

const ACTION_META: Record<string, ActionMeta> = {
  "booking.create": { verb: "created a booking", icon: CalendarPlus, tone: "text-green-500" },
  "booking.update": { verb: "updated a booking", icon: Pencil, tone: "text-amber-500" },
  "booking.delete": { verb: "deleted a booking", icon: Trash2, tone: "text-red-500" },
  "booking.cancel": { verb: "cancelled a booking", icon: XCircle, tone: "text-red-500" },
  "auth.login": { verb: "signed in", icon: LogIn, tone: "text-blue-500" },
  "auth.logout": { verb: "signed out", icon: LogOut, tone: "text-blue-500" },
  "auth.register": { verb: "created an account", icon: UserPlus, tone: "text-green-500" },
  "auth.login_failed": { verb: "failed to sign in", icon: ShieldAlert, tone: "text-red-500" },
  "auth.password_reset": { verb: "reset a password", icon: KeyRound, tone: "text-amber-500" },
  "auth.password_change": { verb: "changed a password", icon: KeyRound, tone: "text-amber-500" },
  "account.profile_update": { verb: "updated their profile", icon: UserCog, tone: "text-blue-500" },
  "account.plan_change": { verb: "changed the plan", icon: CreditCard, tone: "text-purple-500" },
  "config.update": { verb: "updated settings", icon: Settings2, tone: "text-slate-400" },
  "apikey.rotate": { verb: "rotated an API key", icon: RefreshCw, tone: "text-amber-500" },
  "org.create": { verb: "created the organization", icon: Plus, tone: "text-green-500" },
  "org.update": { verb: "updated organization settings", icon: Building2, tone: "text-blue-500" },
  "org.suspend": { verb: "suspended the organization", icon: ShieldAlert, tone: "text-red-500" },
  "org.restore": { verb: "restored the organization", icon: CheckCircle2, tone: "text-green-500" },
  "org.delete": { verb: "deleted the organization", icon: Trash2, tone: "text-red-500" },
  "user.disable": { verb: "disabled a user", icon: UserX, tone: "text-red-500" },
  "user.enable": { verb: "enabled a user", icon: UserCheck, tone: "text-green-500" },
  "conversation.takeover": { verb: "took over a conversation", icon: MessageSquare, tone: "text-blue-500" },
  "conversation.release": { verb: "released a conversation", icon: MessageSquare, tone: "text-slate-400" },
  "impersonation.start": { verb: "started impersonating a user", icon: UserCog, tone: "text-purple-500" },
  "impersonation.end": { verb: "ended impersonation", icon: UserCog, tone: "text-purple-500" },
};

const FALLBACK_META: ActionMeta = { verb: "performed an action", icon: ScrollText, tone: "text-slate-400" };

const RESOURCE_LABELS: Record<string, string> = {
  booking: "a booking",
  user: "a user",
  config: "settings",
  conversation: "a conversation",
  apikey: "an API key",
  org: "the organization",
};

function displayName(email: string | null): string {
  if (!email) return "System";
  const local = email.split("@")[0].replace(/[._-]+/g, " ").trim();
  if (!local) return email;
  const parts = local.split(/\s+/).filter(Boolean);
  const named = parts.map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(" ");
  return named.length > 26 ? parts[0].charAt(0).toUpperCase() + parts[0].slice(1) : named;
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (sameDay(d, today)) return "Today";
  if (sameDay(d, yesterday)) return "Yesterday";
  return d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric", year: "numeric" });
}

function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function searchable(log: AuditLogItem): string {
  const meta = ACTION_META[log.action] ?? FALLBACK_META;
  return [
    displayName(log.actor_email),
    meta.verb,
    log.action,
    log.resource_type,
    log.resource_id,
    log.actor_email,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function TechDetail({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className={`text-[12px] text-foreground ${mono ? "font-mono" : ""}`}>{value || "—"}</div>
    </div>
  );
}

function JsonValue({ label, value }: { label: string; value: Record<string, unknown> | null }) {
  if (!value) return null;
  const entries = Object.entries(value);
  const isFlat = entries.every(([, v]) => typeof v !== "object" || v === null);
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</div>
      {isFlat ? (
        <div className="mt-1 space-y-1">
          {entries.map(([k, v]) => (
            <div key={k} className="flex gap-2 text-[12px]">
              <span className="text-muted-foreground capitalize">{k.replace(/_/g, " ")}:</span>
              <span className="text-foreground">{String(v)}</span>
            </div>
          ))}
        </div>
      ) : (
        <pre className="mt-1 overflow-x-auto rounded-md bg-background/80 border border-border p-2 text-[11px] text-muted-foreground font-mono">
          {JSON.stringify(value, null, 2)}
        </pre>
      )}
    </div>
  );
}

export default function AuditPage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const { data, loading, error, refetch } = useAuditLogs(fromDate, toDate);

  const filtered = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    if (!q) return data;
    return data.filter((log) => searchable(log).includes(q));
  }, [data, search]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);

  const pageGroups = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    const end = start + PAGE_SIZE;
    const sliced = filtered.slice(start, end);
    const groups: { label: string; items: AuditLogItem[] }[] = [];
    for (const log of sliced) {
      const label = dayLabel(log.created_at);
      const last = groups[groups.length - 1];
      if (last && last.label === label) last.items.push(log);
      else groups.push({ label, items: [log] });
    }
    return groups;
  }, [filtered, currentPage]);

  return (
    <>
      <div className="flex items-end justify-between mb-2">
        <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">
            Audit Log
          </h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1">
            A simple record of what happened and who did it.
          </p>
        </div>
        <button
          onClick={() => void refetch()}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
        >
          Refresh
        </button>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search actions, people, resources…" className="w-full sm:w-72" />
          <DateFilter
            label="Date"
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={(v) => { setFromDate(v); setPage(1); }}
            onToChange={(v) => { setToDate(v); setPage(1); }}
          />
          <span className="text-xs text-muted-foreground">
            {filtered.length} event{filtered.length === 1 ? "" : "s"}
          </span>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 text-muted-foreground text-sm py-12">
            <Loader2 className="size-4 animate-spin" /> Loading audit logs...
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 text-red-500 py-12">
            <AlertCircle className="size-4" /> {error}
          </div>
        ) : !data || data.length === 0 ? (
          <div className="text-center py-12">
            <ScrollText className="size-10 text-muted-foreground/40 mx-auto mb-3" />
            <p className="text-muted-foreground text-sm">No audit events yet.</p>
            <p className="text-muted-foreground/60 text-xs mt-1">
              Sensitive actions like cancellations, logins and settings changes will appear here.
            </p>
          </div>
        ) : (
          <>
            <div className="space-y-5">
              {pageGroups.map((group) => (
                <div key={group.label}>
                  <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground/70 mb-2">
                    {group.label}
                  </div>
                  <div className="space-y-2">
                    {group.items.map((log) => {
                      const meta = ACTION_META[log.action] ?? FALLBACK_META;
                      const Icon = meta.icon;
                      const isOpen = expanded === log.id;
                      const subject = displayName(log.actor_email);
                      const resourceLabel = RESOURCE_LABELS[log.resource_type] ?? log.resource_type;
                      return (
                        <div key={log.id} className="border border-border rounded-lg bg-background/50">
                          <button
                            onClick={() => setExpanded(isOpen ? null : log.id)}
                            className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-secondary/40 transition-colors"
                          >
                            {isOpen ? (
                              <ChevronDown className="size-4 text-muted-foreground shrink-0" />
                            ) : (
                              <ChevronRight className="size-4 text-muted-foreground shrink-0" />
                            )}
                            <span className={`flex items-center justify-center size-8 rounded-full bg-background border border-border shrink-0 ${meta.tone}`}>
                              <Icon className="size-4" />
                            </span>
                            <span className="flex-1 min-w-0 text-sm text-foreground">
                              <span className="font-medium">{subject}</span>{" "}
                              <span>{meta.verb}</span>
                              <span className="text-muted-foreground"> on {resourceLabel}</span>
                              {log.resource_id && (
                                <span className="text-muted-foreground/60 font-mono text-[11px]"> #{log.resource_id.slice(0, 8)}</span>
                              )}
                            </span>
                            <span className="text-[11px] text-muted-foreground shrink-0">{timeLabel(log.created_at)}</span>
                          </button>
                          {isOpen && (
                            <div className="px-4 pb-4 pt-3 border-t border-border">
                              <div className="text-[10px] uppercase tracking-wide text-muted-foreground/70 mb-2">
                                Technical details
                              </div>
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2">
                                <TechDetail label="Action" value={log.action} mono />
                                {log.resource_id === log.actor_id && log.resource_id ? (
                                  <TechDetail label="ID" value={log.resource_id} mono />
                                ) : (
                                  <>
                                    {log.resource_id && <TechDetail label="Resource ID" value={log.resource_id} mono />}
                                    {log.actor_id && <TechDetail label="Actor ID" value={log.actor_id} mono />}
                                  </>
                                )}
                                <TechDetail label="Actor" value={log.actor_email ?? "System"} />
                              </div>
                              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-3">
                                <JsonValue label="Before" value={log.old_values} />
                                <JsonValue label="After" value={log.new_values} />
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
            <Pagination page={currentPage} pageSize={PAGE_SIZE} total={filtered.length} onPageChange={setPage} />
          </>
        )}
      </div>
    </>
  );
}