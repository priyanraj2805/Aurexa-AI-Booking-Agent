"use client";

import { useState, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, AlertCircle, CalendarX2, ArrowLeft, SlidersHorizontal, ChevronDown, X } from "lucide-react";
import Link from "next/link";
import { useCancelledBookings } from "@/hooks/use-dashboard";
import { Pagination } from "@/components/pagination";
import { SearchInput, DateFilter, FilterSelect } from "@/components/filter-bar";

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
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [unitFilter, setUnitFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [filterUnits, setFilterUnits] = useState<{ id: string; name: string }[]>([]);
  const [filterProviders, setFilterProviders] = useState<{ id: string; full_name: string; units: string[] }[]>([]);

  useEffect(() => {
    import("@/lib/org-api").then(({ orgApi }) =>
      Promise.all([orgApi.units().catch(() => []), orgApi.providers().catch(() => [])]).then(([u, p]) => {
        setFilterUnits((u as { id: string; name: string }[]).map((x) => ({ id: x.id, name: x.name })));
        setFilterProviders(
          (p as { id: string; full_name: string; units: string[] }[]).map((x) => ({ id: x.id, full_name: x.full_name, units: x.units || [] }))
        );
      })
    ).catch(() => {});
  }, []);

  const visibleProviders = unitFilter ? filterProviders.filter((p) => p.units.includes(unitFilter)) : filterProviders;
  useEffect(() => {
    if (unitFilter && providerFilter && !visibleProviders.some((p) => p.id === providerFilter)) {
      setProviderFilter("");
      setPage(1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unitFilter]);

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

  const { data, loading, error } = useCancelledBookings(page, 10, { search, fromDate, toDate, unit_id: unitFilter || undefined, provider_id: providerFilter || undefined });

  const isHighlighted = (b: { id: string; booking_id?: string | null }) =>
    highlightedId === b.id || (b.booking_id != null && highlightedId === b.booking_id);

  useEffect(() => {
    if (!highlightedId) return;
    const t = setTimeout(() => {
      const el =
        document.querySelector(`[data-booking-id="${highlightedId}"]`) ??
        document.querySelector(`[data-cancelled-booking-id="${highlightedId}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 300);
    return () => clearTimeout(t);
  }, [highlightedId, data]);

  return (
    <>
      <style>{`@keyframes highlight { 0% { background-color: hsl(var(--primary) / 0.25); transform: scale(1.01); } 50% { background-color: hsl(var(--primary) / 0.15); } 100% { background-color: transparent; transform: scale(1); } }`}</style>
      <div className="mb-3 flex flex-col items-start gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">
            Cancelled Bookings
          </h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1">
            Bookings that were cancelled, either by the customer or by an admin.
          </p>
        </div>
        <Link
          href="/dashboard/bookings"
          className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="size-3.5" /> All bookings
        </Link>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="mb-4 flex min-w-0 items-center gap-2">
          <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search bookings…" className="min-w-0 flex-1 xl:hidden" />
          <button
            type="button"
            onClick={() => setFiltersOpen((open) => !open)}
            aria-expanded={filtersOpen}
            className={`inline-flex h-10 shrink-0 items-center gap-2 rounded-md border px-3 text-sm transition-colors xl:hidden ${
              filtersOpen || fromDate || toDate || unitFilter || providerFilter
                ? "border-primary/40 bg-primary/5 text-primary"
                : "border-border bg-background text-muted-foreground hover:text-foreground"
            }`}
          >
            <SlidersHorizontal className="size-4" />
            <span>Filters</span>
            <ChevronDown className={`size-4 transition-transform ${filtersOpen ? "rotate-180" : ""}`} />
          </button>
        </div>
        <div className={`mb-4 min-w-0 grid-cols-1 gap-3 min-[360px]:grid-cols-2 xl:flex xl:flex-wrap xl:items-center ${filtersOpen ? "grid" : "hidden xl:flex"}`}>
          <SearchInput value={search} onChange={(v) => { setSearch(v); setPage(1); }} placeholder="Search bookings…" className="hidden w-56 shrink-0 xl:block" />
          <DateFilter
            label="Cancelled on"
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={(v) => { setFromDate(v); setPage(1); }}
            onToChange={(v) => { setToDate(v); setPage(1); }}
            className="min-w-0 min-[360px]:col-span-2 xl:col-span-1"
          />
          {!!filterUnits.length && (
            <FilterSelect
              value={unitFilter}
              onChange={(value) => { setUnitFilter(value); setPage(1); }}
              options={[
                { value: "", label: "All departments" },
                ...filterUnits.map((u) => ({ value: u.id, label: u.name })),
              ]}
            />
          )}
          {!!filterProviders.length && (
            <FilterSelect
              value={providerFilter}
              onChange={(value) => { setProviderFilter(value); setPage(1); }}
              options={[
                { value: "", label: "All members" },
                ...visibleProviders.map((p) => ({ value: p.id, label: p.full_name })),
              ]}
            />
          )}
          {(search || fromDate || toDate || unitFilter || providerFilter) && (
            <button
              type="button"
              onClick={() => {
                setSearch("");
                setFromDate("");
                setToDate("");
                setUnitFilter("");
                setProviderFilter("");
                setPage(1);
              }}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              Clear filters <X className="size-3.5" />
            </button>
          )}
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
              {search || fromDate || toDate || unitFilter || providerFilter
                ? "Try clearing the search or date filters."
                : "Cancelled bookings will show up here."}
            </p>
          </div>
        ) : (
          <>
            <div className="w-full overflow-x-auto">
              {/* Mobile card view */}
              <div className="xl:hidden space-y-3">
                {data.items.map((b) => (
                  <div
                    key={b.id}
                    data-booking-id={b.id}
                    data-cancelled-booking-id={b.booking_id ?? undefined}
                    className={`flex items-start gap-3 p-4 border rounded-lg bg-background/50 transition-all ${
                      isHighlighted(b) ? "border-primary bg-primary/10 ring-2 ring-primary/30 animate-pulse" : "border-border"
                    }`}
                  >
                    <div className="flex items-center justify-center size-8 rounded-full bg-red-500/10 text-red-500 text-xs font-semibold shrink-0">
                      {getInitials(b.customer_name)}
                    </div>
                    <div className="flex-1 min-w-0 space-y-1">
                      <div className="font-medium text-[13px]">{b.customer_name}</div>
                      {(b.unit_name || b.provider_name) && (
                        <div className="flex gap-1">
                          {b.unit_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary">{b.unit_name}</span>}
                          {b.provider_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">{b.provider_name}</span>}
                        </div>
                      )}
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
              <table className="hidden xl:table w-full border-collapse">
                <thead>
                      <tr>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Client</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Email</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Phone</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Booked For</th>
                        <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Cancelled At</th>
                      </tr>
                </thead>
                <tbody>
                  {data.items.map((b) => (
                    <tr
                      key={b.id}
                      data-booking-id={b.id}
                      data-cancelled-booking-id={b.booking_id ?? undefined}
                      className={`hover:bg-secondary/50 transition-colors ${isHighlighted(b) ? "bg-primary/10 ring-2 ring-inset ring-primary/30 animate-pulse" : ""}`}
                    >
                      <td className="px-4 py-4 text-[13px] border-b border-border">
                        <div className="flex items-center gap-3">
                          <div className="flex items-center justify-center size-8 rounded-full bg-red-500/10 text-red-500 text-xs font-semibold">
                            {getInitials(b.customer_name)}
                          </div>
                          <div>
                            <div className="font-medium">{b.customer_name}</div>
                            {(b.unit_name || b.provider_name) && (
                              <div className="flex gap-1 mt-1">
                                {b.unit_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary">{b.unit_name}</span>}
                                {b.provider_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">{b.provider_name}</span>}
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-4 text-[13px] border-b border-border">{b.customer_email || "—"}</td>
                      <td className="px-4 py-4 text-[13px] border-b border-border">{b.customer_phone ?? "—"}</td>
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
