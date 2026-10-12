"use client";

import { useState, useEffect } from "react";
import { Loader2, AlertCircle, CalendarCheck, ExternalLink, X, Video, Building2, Link2, Radio } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useBookings } from "@/hooks/use-dashboard";
import { Pagination } from "@/components/pagination";
import { SearchInput, DateFilter } from "@/components/filter-bar";
import { api } from "@/lib/api";
import { toast } from "sonner";

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

function MeetingModeBadge({ booking }: { booking: { meeting_type_snapshot?: string | null; meeting_link_status?: string | null; meeting_link_provider?: string | null; meeting_link_url?: string | null } }) {
  const isOnline = booking.meeting_type_snapshot === "ONLINE";
  const provider = booking.meeting_link_provider?.replace("_", " ") || "";
  const status = booking.meeting_link_status;
  const isReady = status === "READY";
  const isUnavailable = status === "UNAVAILABLE";

  if (!booking.meeting_type_snapshot) {
    return <span className="inline-flex items-center px-2 py-1 rounded-full text-[10px] bg-muted text-muted-foreground">—</span>;
  }

  if (!isOnline) {
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-gradient-to-r from-zinc-100 to-zinc-200 dark:from-zinc-800 dark:to-zinc-700 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700">
        <Building2 className="size-3" />
        Offline
      </span>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-muted text-muted-foreground border border-border w-fit">
        <Video className="size-3" />
        Online
      </span>
      {booking.meeting_link_url && booking.meeting_link_status === "READY" && (
        <a href={booking.meeting_link_url} target="_blank" rel="noopener noreferrer" className="text-[13px] font-semibold text-blue-600 hover:text-blue-700 dark:text-blue-400 hover:underline">
          link
        </a>
      )}
      {booking.meeting_link_status === "UNAVAILABLE" && (
        <span className="text-[10px] text-amber-600">Link unavailable</span>
      )}
    </div>
  );
}

export default function BookingsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [status, setStatus] = useState<string>("");
  const [attendanceStatus, setAttendanceStatus] = useState<string>("");
  const [meetingModeFilter, setMeetingModeFilter] = useState<string>("");
  const [unitFilter, setUnitFilter] = useState<string>("");
  const [providerFilter, setProviderFilter] = useState<string>("");
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

  // Doctor list follows the selected department.
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
      // Clear highlight after animation duration
      const t = setTimeout(() => setHighlightedId(null), 3500);
      return () => clearTimeout(t);
    }
  }, [searchParams]);

  const { data, loading, error, refetch } = useBookings(page, 10, { search, status, attendance_status: attendanceStatus, meeting_mode: meetingModeFilter, fromDate, toDate, unit_id: unitFilter || undefined, provider_id: providerFilter || undefined });

  const handleAttendanceChange = async (bookingId: string, newStatus: string) => {
    try {
      await api.put(`/dashboard/bookings/${bookingId}/attendance`, { attendance_status: newStatus });
      toast.success(`Attendance marked as ${newStatus}`);
      refetch();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to update attendance");
    }
  };

  useEffect(() => {
    if (!highlightedId) return;
    // Scroll to highlighted booking after data loads
    const t = setTimeout(() => {
      const el = document.querySelector(`[data-booking-id="${highlightedId}"]`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    }, 300);
    return () => clearTimeout(t);
  }, [highlightedId, data]);

  useEffect(() => {
    const s = searchParams.get("status");
    if (s) setStatus(s);
    const q = searchParams.get("search");
    if (q) {
      setSearch(q);
      setPage(1);
    }
  }, [searchParams]);

  return (
    <>
      <style>{`@keyframes highlight { 0% { background-color: hsl(var(--primary) / 0.25); transform: scale(1.01); } 50% { background-color: hsl(var(--primary) / 0.15); } 100% { background-color: transparent; transform: scale(1); } }`}</style>
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
          <select
            value={attendanceStatus}
            onChange={(e) => { setAttendanceStatus(e.target.value); setPage(1); }}
            className="h-9 rounded-md border border-border bg-background px-3 text-xs"
          >
            <option value="">All Statuses</option>
            <option value="SCHEDULED">Scheduled</option>
            <option value="ATTENDED">Attended</option>
            <option value="NO_SHOW">No-Show</option>
            <option value="CANCELLED">Cancelled</option>
            <option value="RESCHEDULED">Rescheduled</option>
          </select>
          <select
            value={meetingModeFilter}
            onChange={(e) => { setMeetingModeFilter(e.target.value); setPage(1); }}
            className="h-9 rounded-md border border-border bg-background px-3 text-xs"
          >
            <option value="">All Modes</option>
            <option value="ONLINE">Online</option>
            <option value="OFFLINE">Offline</option>
          </select>
          {!!filterUnits.length && (
            <select
              value={unitFilter}
              onChange={(e) => { setUnitFilter(e.target.value); setPage(1); }}
              className="h-9 rounded-md border border-border bg-background px-3 text-xs"
            >
              <option value="">All departments</option>
              {filterUnits.map((u) => (
                <option key={u.id} value={u.id}>{u.name}</option>
              ))}
            </select>
          )}
          {!!filterProviders.length && (
            <select
              value={providerFilter}
              onChange={(e) => { setProviderFilter(e.target.value); setPage(1); }}
              className="h-9 rounded-md border border-border bg-background px-3 text-xs"
            >
              <option value="">All members</option>
              {visibleProviders.map((p) => (
                <option key={p.id} value={p.id}>{p.full_name}</option>
              ))}
            </select>
          )}
          {(status || attendanceStatus || meetingModeFilter || unitFilter || providerFilter) && (
            <button
              onClick={() => { setStatus(""); setAttendanceStatus(""); setMeetingModeFilter(""); setUnitFilter(""); setProviderFilter(""); setPage(1); }}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              Clear filters <X className="size-3.5" />
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
                  <div
                    key={b.id}
                    data-booking-id={b.id}
                    className={`flex items-start gap-3 p-4 border rounded-lg bg-background/50 transition-all ${
                      highlightedId === b.id
                        ? "border-primary bg-primary/10 ring-2 ring-primary/30 animate-pulse"
                        : "border-border"
                    }`}
                  >
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
                      {(b.unit_name || b.provider_name) && (
                        <div className="flex gap-1">
                          {b.unit_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary">{b.unit_name}</span>}
                          {b.provider_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">{b.provider_name}</span>}
                        </div>
                      )}
                      <MeetingModeBadge booking={b} />
                      <div className="flex items-center gap-1.5 mt-1">
                        <select
                          value={b.attendance_status || "SCHEDULED"}
                          onChange={(e) => handleAttendanceChange(b.id, e.target.value)}
                          className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border focus:outline-none focus:ring-1 focus:ring-primary ${b.attendance_status === "ATTENDED" ? "bg-green-500/10 text-green-600 border-green-500/20" : b.attendance_status === "NO_SHOW" ? "bg-red-500/10 text-red-600 border-red-500/20" : b.attendance_status === "CANCELLED" ? "bg-red-500/10 text-red-600 border-red-500/20" : b.attendance_status === "RESCHEDULED" ? "bg-blue-500/10 text-blue-600 border-blue-500/20" : "bg-green-500/10 text-green-600 border-green-500/20"}`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <option value="SCHEDULED">Scheduled</option>
                          <option value="ATTENDED">Attended</option>
                          <option value="NO_SHOW">No-Show</option>
                          <option value="CANCELLED">Cancelled</option>
                          <option value="RESCHEDULED">Rescheduled</option>
                        </select>
                        {b.meeting_type_snapshot === "ONLINE" && b.attendance_status === "SCHEDULED" && <span className="text-[10px] text-muted-foreground">• Join</span>}
                        {b.meeting_type_snapshot === "OFFLINE" && b.attendance_status === "SCHEDULED" && <span className="text-[10px] text-muted-foreground">• Mark after</span>}
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
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Mode</th>
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Appointment Status</th>
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Booked At</th>
                    <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((b) => (
                    <tr
                      key={b.id}
                      data-booking-id={b.id}
                      className={`hover:bg-secondary/50 transition-colors ${
                        highlightedId === b.id ? "bg-primary/10 ring-2 ring-inset ring-primary/30 animate-pulse" : ""
                      }`}
                    >
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
                            {(b.unit_name || b.provider_name) && (
                              <div className="flex gap-1 mt-1">
                                {b.unit_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary">{b.unit_name}</span>}
                                {b.provider_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">{b.provider_name}</span>}
                              </div>
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
                        <MeetingModeBadge booking={b} />
                      </td>
                      <td className="px-4 py-4 text-[13px] border-b border-border">
                        <select
                          value={b.attendance_status || "SCHEDULED"}
                          onChange={(e) => handleAttendanceChange(b.id, e.target.value)}
                          className={`text-[10px] font-semibold px-2 py-1 rounded-full border focus:outline-none focus:ring-1 focus:ring-primary ${b.attendance_status === "ATTENDED" ? "bg-green-500/10 text-green-600 border-green-500/20" : b.attendance_status === "NO_SHOW" ? "bg-red-500/10 text-red-600 border-red-500/20" : b.attendance_status === "CANCELLED" ? "bg-red-500/10 text-red-600 border-red-500/20" : b.attendance_status === "RESCHEDULED" ? "bg-blue-500/10 text-blue-600 border-blue-500/20" : "bg-green-500/10 text-green-600 border-green-500/20"}`}
                          onClick={(e) => e.stopPropagation()}
                        >
                          <option value="SCHEDULED">Scheduled</option>
                          <option value="ATTENDED">Attended</option>
                          <option value="NO_SHOW">No-Show</option>
                          <option value="CANCELLED">Cancelled</option>
                          <option value="RESCHEDULED">Rescheduled</option>
                        </select>
                        {b.meeting_type_snapshot === "ONLINE" && b.attendance_status === "SCHEDULED" && <div className="text-[10px] text-muted-foreground mt-1">Join to mark</div>}
                        {b.meeting_type_snapshot === "OFFLINE" && b.attendance_status === "SCHEDULED" && <div className="text-[10px] text-muted-foreground mt-1">Mark after</div>}
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
