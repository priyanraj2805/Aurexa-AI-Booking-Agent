"use client";

import { useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, AlertCircle, CalendarX2, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useCancelledBookings } from "@/hooks/use-dashboard";
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

export default function CancelledBookingsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");

  const searchParams = useSearchParams();
  const highlightId = searchParams.get("highlight");
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  useEffect(() => {
    const h = searchParams.get("highlight");
    if (h) {
      setHighlightedId(h);
      const t = setTimeout(() => setHighlightedId(null), 3500);
      return () => clearTimeout(t);
    }
  }, [searchParams]);

  const { data, loading, error } = useCancelledBookings(page, 10, { search, fromDate, toDate });

  useEffect(() => {
    if (!highlightedId) return;
    const t = setTimeout(() => {
      const el = document.querySelector(`[data-booking-id="${highlightedId}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 300);
    return () => clearTimeout(t);
  }, [highlightedId, data]);

  return (
    <>
      <style>{`@keyframes highlight { 0% { background-color: hsl(var(--primary) / 0.25); transform: scale(1.01); } 50% { background-color: hsl(var(--primary) / 0.15); } 100% { background-color: transparent; transform: scale(1); } }`}</style>
      <div className="flex items-end justify-between mb-2">
        <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">
            Cancelled Bookings
          </h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1">
            Bookings that were cancelled, either by the customer or by an admin.
          </p>
        </div>
        <Link
          href="/dashboard/bookings"
          className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="size-3.5" /> All bookings
        </Link>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="flex flex-wrap items-center gap-3 mb-4">
          <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search name or email…" className="w-full sm:w-72" />
          <DateFilter
            label="Cancelled on"
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={(v) => { setFromDate(v); setPage(1); }}
            onToChange={(v) => { setToDate(v); setPage(1); }}
          />
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 text-muted-foreground text-sm py-12">
            <Loader2 className="size-4 animate-spin" /> Loading cancelled bookings...
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 text-red-500 py-12">
            <AlertCircle className="size-4" /> {error}
          </div>
        ) : !data || data.items.length === 0 ? (
          <div className="text-center py-12">
            <CalendarX2 className="size-10 text-muted-foreground/40 mx-auto mb-3" />
            <p className="text-muted-foreground text-sm">No cancelled bookings found.</p>
            <p className="text-muted-foreground/60 text-xs mt-1">
              {search || fromDate || toDate
                ? "Try clearing the search or date filters."
                : "Cancelled bookings will show up here."}
            </p>
          </div>
        ) : (
          <>
            <div className="w-full overflow-x-auto">
              {/* Mobile card view */}
              <div className="md:hidden space-y-3">
                {data.items.map((b) => (
                  <div
                    key={b.id}
                    data-booking-id={b.id}
                    className={`flex items-start gap-3 p-4 border rounded-lg bg-background/50 transition-all ${
                      highlightedId === b.id ? "border-primary bg-primary/10 ring-2 ring-primary/30 animate-pulse" : "border-border"
                    }`}
                  >
                    <div className="flex items-center justify-center size-8 rounded-full bg-red-500/10 text-red-500 text-xs font-semibold shrink-0">
                      {getInitials(b.customer_name)}
                    </div>
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="font-medium text-[13px]">{b.customer_name}</div>
                      <div className="text-[11px] text-muted-foreground truncate">{b.customer_email}</div>
                      {b.customer_phone && (
                        <div className="text-[11px] text-muted-foreground">{b.customer_phone}</div>
                      )}
                      {b.booking_date && (
                        <div className="text-[11px] text-muted-foreground">
                          Was booked for: {b.booking_date}
                          {b.booking_time ? ` at ${b.booking_time}` : ""}
                        </div>
                      )}
                      <div className="text-[11px] text-red-500/80">
                        Cancelled: {formatDateTime(b.cancelled_at)}
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
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Booked For</th>
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Cancelled At</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((b) => (
                    <tr
                      key={b.id}
                      data-booking-id={b.id}
                      className={`hover:bg-secondary/50 transition-colors ${highlightedId === b.id ? "bg-primary/10 ring-2 ring-inset ring-primary/30 animate-pulse" : ""}`}
                    >
                      <td className="px-4 py-4 text-[13px] border-b border-border">
                        <div className="flex items-center gap-3">
                          <div className="flex items-center justify-center size-8 rounded-full bg-red-500/10 text-red-500 text-xs font-semibold">
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
                        {b.booking_date ?? "—"}
                        {b.booking_time ? ` at ${b.booking_time}` : ""}
                      </td>
                      <td className="px-4 py-4 text-[13px] border-b border-border text-red-500/80">
                        {formatDateTime(b.cancelled_at)}
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
