"use client";

import { useState, useEffect } from "react";
import { Loader2, AlertCircle, CalendarCheck, ExternalLink, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useBookings } from "@/hooks/use-dashboard";
import { Pagination } from "@/components/pagination";
import { SearchInput, DateFilter } from "@/components/filter-bar";

function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
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

function statusBadge(status: string | null) {
  switch (status) {
    case "CONFIRMED":
      return "bg-green-500/10 text-green-500";
    case "CANCELLED":
      return "bg-red-500/10 text-red-500";
    case "ACTIVE":
    case "CONFIRMING":
      return "bg-yellow-500/10 text-yellow-500";
    default:
      return "bg-white/10 text-muted-foreground";
  }
}

export default function BookingsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [status, setStatus] = useState<string>("");

  const searchParams = useSearchParams();
  useEffect(() => {
    const s = searchParams.get("status");
    if (s) setStatus(s);
    const q = searchParams.get("search");
    if (q) {
      setSearch(q);
      setPage(1);
    }
  }, [searchParams]);

  const { data, loading, error } = useBookings(page, 10, { search, status, fromDate, toDate });

  return (
    <>
      <div className="flex items-end justify-between mb-2">
        <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">
            Bookings
            {status && (
              <span className="ml-2 inline-flex items-center gap-1.5 text-sm font-medium px-2 py-1 rounded-full bg-primary/10 text-primary">
                {status}
              </span>
            )}
          </h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1">
            Every booking made by your AI agent, searchable and date-filtered.
          </p>
        </div>
        <Link
          href="/dashboard/bookings/cancelled"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-red-500 hover:text-red-400 transition-colors"
        >
          View cancelled bookings <ExternalLink className="size-3.5" />
        </Link>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search name, email, phone…" className="w-full sm:w-72" />
          <DateFilter
            label="Date range"
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={(v) => { setFromDate(v); setPage(1); }}
            onToChange={(v) => { setToDate(v); setPage(1); }}
          />
          {status && (
            <button
              onClick={() => { setStatus(""); setPage(1); }}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              Clear filter <X className="size-3.5" />
            </button>
          )}
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 text-muted-foreground text-sm py-12">
            <Loader2 className="size-4 animate-spin" /> Loading bookings...
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 text-red-500 py-12">
            <AlertCircle className="size-4" /> {error}
          </div>
        ) : !data || data.items.length === 0 ? (
          <div className="text-center py-12">
            <CalendarCheck className="size-10 text-muted-foreground/40 mx-auto mb-3" />
            <p className="text-muted-foreground text-sm">No bookings found.</p>
            <p className="text-muted-foreground/60 text-xs mt-1">
              {search || fromDate || toDate
                ? "Try clearing the search or date filters."
                : "Deploy your widget and get your first booking to see it here."}
            </p>
          </div>
        ) : (
          <>
            <div className="w-full overflow-x-auto">
              {/* Mobile card view */}
              <div className="md:hidden space-y-3">
                {data.items.map((b) => (
                  <div key={b.id} className="flex items-start gap-3 p-4 border border-border rounded-lg bg-background/50">
                    <div className="flex items-center justify-center size-8 rounded-full bg-[#27272a] text-xs font-semibold shrink-0">
                      {getInitials(b.customer_name)}
                    </div>
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="flex items-center justify-between gap-2">
                        <div className="font-medium text-[13px]">{b.customer_name}</div>
                        <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full ${statusBadge(b.session_status)}`}>
                          {b.session_status ?? "—"}
                        </span>
                      </div>
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
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Client</th>
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Email</th>
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Meeting Time</th>
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Booked At</th>
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((b) => (
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
                      <td className="px-4 py-4 text-[13px] border-b border-border">{formatDateTime(b.created_at)}</td>
                      <td className="px-4 py-4 border-b border-border">
                        <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full ${statusBadge(b.session_status)}`}>
                          {b.session_status ?? "—"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPageChange={setPage} />
          </>
        )}
      </div>
    </>
  );
}
