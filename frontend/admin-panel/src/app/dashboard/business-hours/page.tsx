"use client";

import { useState, useCallback, useEffect, useRef, useMemo, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  Loader2, AlertCircle, Save, Plus, Trash2, CalendarOff, Clock, Eye, Calendar, ChevronDown,
} from "lucide-react";
import { api } from "@/lib/api";
import {
  useSchedule, useExceptions, useSlotPreview,
  type ScheduleDay, type ScheduleConfig, type ExceptionItem,
} from "@/hooks/use-dashboard";
import { LoaderPulseSquare } from "@/components/ui/loaders";
import { toast } from "sonner";


// ── Constants ──────────────────────────────────────────────────────────────────

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

// Leap-aware horizon ceiling: 366 only when a Feb 29 falls inside the next
// 366 days, otherwise 365 (mirrors backend max_horizon_days).
function maxHorizonDays(now: Date = new Date()): number {
  const isLeap = (y: number) => y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0);
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const end = new Date(start.getTime() + 366 * 864e5);
  for (let y = start.getFullYear(); y <= end.getFullYear(); y++) {
    if (!isLeap(y)) continue;
    const feb29 = new Date(y, 1, 29);
    if (feb29 >= start && feb29 <= end) return 366;
  }
  return 365;
}

// Full IANA timezone list at runtime (all zones supported by the browser),
// with a curated fallback for engines without Intl.supportedValuesOf.
const FALLBACK_TIMEZONES = [
  "UTC", "Africa/Abidjan", "Africa/Cairo", "Africa/Casablanca", "Africa/Johannesburg", "Africa/Lagos",
  "Africa/Nairobi", "America/Argentina/Buenos_Aires", "America/Bogota", "America/Caracas",
  "America/Chicago", "America/Denver", "America/Halifax", "America/Lima", "America/Los_Angeles",
  "America/Mexico_City", "America/New_York", "America/Phoenix", "America/Santiago", "America/Sao_Paulo",
  "America/Toronto", "America/Vancouver", "Asia/Almaty", "Asia/Baghdad", "Asia/Bangkok", "Asia/Beirut",
  "Asia/Dhaka", "Asia/Dubai", "Asia/Hong_Kong", "Asia/Istanbul", "Asia/Jakarta", "Asia/Jerusalem",
  "Asia/Karachi", "Asia/Kathmandu", "Asia/Kolkata", "Asia/Kuala_Lumpur", "Asia/Manila", "Asia/Riyadh",
  "Asia/Seoul", "Asia/Shanghai", "Asia/Singapore", "Asia/Taipei", "Asia/Tashkent", "Asia/Tehran",
  "Asia/Tokyo", "Asia/Yangon", "Australia/Adelaide", "Australia/Brisbane", "Australia/Melbourne",
  "Australia/Perth", "Australia/Sydney", "Europe/Amsterdam", "Europe/Athens", "Europe/Berlin",
  "Europe/Brussels", "Europe/Bucharest", "Europe/Budapest", "Europe/Copenhagen", "Europe/Dublin",
  "Europe/Helsinki", "Europe/Lisbon", "Europe/London", "Europe/Madrid", "Europe/Moscow", "Europe/Oslo",
  "Europe/Paris", "Europe/Prague", "Europe/Rome", "Europe/Stockholm", "Europe/Vienna", "Europe/Warsaw",
  "Europe/Zurich", "Pacific/Auckland", "Pacific/Fiji", "Pacific/Honolulu",
];

const TIMEZONES: string[] = (() => {
  try {
    const supported = Intl.supportedValuesOf("timeZone");
    if (Array.isArray(supported) && supported.length > 0) return [...supported].sort();
  } catch {
    // Intl.supportedValuesOf unavailable — use the curated list below.
  }
  return FALLBACK_TIMEZONES;
})();

const DURATIONS = [15, 30, 45, 60, 90, 120];

// ── Searchable timezone picker ─────────────────────────────────────────────────

function TimezonePicker({ value, onChange }: { value: string; onChange: (tz: string) => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(value);
  const [highlight, setHighlight] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  const filtered = useMemo(() => {
    const q = text.trim().toLowerCase();
    if (!q) return value ? [value, ...TIMEZONES.filter((tz) => tz !== value)] : TIMEZONES;
    return TIMEZONES.filter((tz) => tz.toLowerCase().includes(q));
  }, [text, value]);

  useEffect(() => setText(value), [value]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => setHighlight(0), [text, open]);

  useEffect(() => {
    const el = listRef.current?.children[highlight] as HTMLElement | undefined;
    el?.scrollIntoView({ block: "nearest" });
  }, [highlight]);

  const select = (tz: string) => {
    setText(tz);
    onChange(tz);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, filtered.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter" && open && filtered[highlight]) {
      e.preventDefault();
      select(filtered[highlight]);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <input
        type="text"
        aria-label="Timezone"
        role="combobox"
        aria-expanded={open}
        value={text}
        placeholder="Search timezone…"
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setText(e.target.value);
          setOpen(true);
        }}
        onKeyDown={onKeyDown}
        className="w-full bg-secondary border border-border rounded-lg px-3 py-2 pr-8 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
      />
      <ChevronDown
        className={`absolute right-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground pointer-events-none transition-transform ${open ? "rotate-180" : ""}`}
      />
      {open && (
        <ul
          ref={listRef}
          className="absolute z-20 mt-1 w-full max-h-56 overflow-auto bg-card border border-border rounded-lg shadow-lg py-1"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-2 text-sm text-muted-foreground">No timezone matches</li>
          ) : (
            filtered.map((tz, i) => (
              <li
                key={tz}
                onMouseDown={(e) => {
                  e.preventDefault();
                  select(tz);
                }}
                onMouseEnter={() => setHighlight(i)}
                className={`px-3 py-1.5 text-sm cursor-pointer ${
                  i === highlight ? "bg-secondary" : ""
                } ${tz === value ? "text-primary font-medium" : ""}`}
              >
                {tz}
              </li>
            ))
          )}
        </ul>
      )}
    </div>
  );
}

// ── Default schedule ───────────────────────────────────────────────────────────

function defaultSchedule(): ScheduleConfig {
  return {
    slot_duration_minutes: 60,
    booking_window_days: 60,
    timezone: "UTC",
    days: Array.from({ length: 7 }, (_, i) => ({
      day_of_week: i,
      is_active: i < 5, // Mon–Fri active by default
      open_time: "09:00",
      close_time: "17:00",
      break_start: null,
      break_end: null,
    })),
  };
}

// ── Weekly Schedule Tab ────────────────────────────────────────────────────────

function WeeklyScheduleTab() {
  const { data: loaded, loading, error, refetch } = useSchedule();
  const [schedule, setSchedule] = useState<ScheduleConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  // Seed form state from API response
  useEffect(() => {
    if (loaded) {
      setSchedule(loaded.days.length > 0 ? loaded : defaultSchedule());
    } else if (!loading && !error) {
      setSchedule(defaultSchedule());
    }
  }, [loaded, loading, error]);

  const updateMeta = (field: keyof Omit<ScheduleConfig, "days">, value: string | number) => {
    setSchedule((prev) => prev ? { ...prev, [field]: value } : prev);
  };

  const updateDay = (idx: number, field: keyof ScheduleDay, value: string | boolean | null) => {
    setSchedule((prev) => {
      if (!prev) return prev;
      const days = prev.days.map((d, i) => {
        if (i !== idx) return d;
        const next: ScheduleDay = { ...d, [field]: value as never };
        // When activating a closed day that has null times (from onboarding), seed defaults
        if (field === "is_active" && value === true) {
          if (!next.open_time) next.open_time = "09:00";
          if (!next.close_time) next.close_time = "17:00";
        }
        return next;
      });
      return { ...prev, days };
    });
  };

  const handleSave = async () => {
    if (!schedule) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api.put("/dashboard/schedule", schedule);
      setSaved(true);
      toast.success("Schedule successfully saved");
      setTimeout(() => setSaved(false), 2000);
      await refetch();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return (
    <div className="space-y-6 animate-pulse mt-4">
      <div className="bg-card border border-border rounded-xl h-[120px]" />
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <div className="h-4 w-32 bg-secondary rounded" />
        </div>
        <div className="divide-y divide-border">
          {[...Array(7)].map((_, i) => (
             <div key={i} className="px-5 py-4 flex flex-col md:flex-row md:items-center gap-4">
               <div className="md:w-32 flex items-center gap-4">
                 <div className="w-9 h-5 bg-secondary rounded-full" />
                 <div className="w-20 h-4 bg-secondary rounded" />
               </div>
               <div className="flex gap-3 flex-1">
                 <div className="h-[34px] w-28 bg-secondary rounded-md" />
                 <div className="h-[34px] w-28 bg-secondary rounded-md" />
               </div>
             </div>
          ))}
        </div>
      </div>
    </div>
  );
  if (error) return <ErrorState message={error} />;
  if (!schedule) return null;

  return (
    <div className="space-y-6">
      {/* Meta row */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-5">
        <h3 className="text-sm font-semibold mb-4 text-muted-foreground uppercase tracking-wider">Global Settings</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1.5">Slot Duration</label>
            <select
              aria-label="Slot Duration"
              value={schedule.slot_duration_minutes}
              onChange={(e) => updateMeta("slot_duration_minutes", Number(e.target.value))}
              className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            >
              {DURATIONS.map((d) => (
                <option key={d} value={d}>{d} min</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1.5">Booking Horizon</label>
            <div className="flex items-center gap-2">
              <input
                aria-label="Booking Horizon"
                type="number"
                value={schedule.booking_window_days}
                min={1}
                max={maxHorizonDays()}
                title={`1–${maxHorizonDays()} days (366 only when the window can include Feb 29 of a leap year)`}
                onChange={(e) => {
                  // A year caps the horizon — leap-aware (366 only when Feb 29
                  // is reachable). Native min/max don't block typing past bounds.
                  const cap = maxHorizonDays();
                  const raw = Number(e.target.value);
                  const clamped = Number.isFinite(raw) ? Math.min(cap, Math.max(1, Math.round(raw))) : 1;
                  updateMeta("booking_window_days", clamped);
                }}
                className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              />
              <span className="text-xs text-muted-foreground whitespace-nowrap">days ahead</span>
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1.5">Timezone</label>
            <TimezonePicker
              value={schedule.timezone}
              onChange={(tz) => updateMeta("timezone", tz)}
            />
          </div>
        </div>
      </div>

      {/* Day rows */}
      <div className="bg-card border border-border rounded-xl overflow-hidden">
        <div className="px-5 py-4 border-b border-border">
          <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">Weekly Hours</h3>
        </div>
        <div className="divide-y divide-border">
          {schedule.days.map((day, idx) => (
            <div key={day.day_of_week} className={`px-4 sm:px-5 py-4 flex flex-col gap-3 transition-colors ${day.is_active ? "" : "opacity-50"}`}>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-3 sm:gap-x-4">
                {/* Toggle */}
                <button
                  aria-label={day.is_active ? `Deactivate ${DAY_NAMES[day.day_of_week]}` : `Activate ${DAY_NAMES[day.day_of_week]}`}
                  onClick={() => updateDay(idx, "is_active", !day.is_active)}
                  className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none ${
                    day.is_active ? "bg-primary" : "bg-secondary"
                  }`}
                >
                  <span className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${day.is_active ? "translate-x-4" : "translate-x-0"}`} />
                </button>
                {/* Day name */}
                <span className="w-20 shrink-0 truncate text-sm font-medium sm:w-24">{DAY_NAMES[day.day_of_week]}</span>
                {/* Times — own grid columns on mobile so open/close sit side-by-side full-width */}
                {day.is_active && (
                  <>
                    <div className="grid min-w-[168px] flex-1 grid-cols-[1fr_auto_1fr] items-center gap-2">
                      <TimeSelect
                        value={day.open_time ?? ""}
                        onChange={(v) => updateDay(idx, "open_time", v)}
                        label="Open"
                      />
                      <span className="text-center text-sm text-muted-foreground">–</span>
                      <TimeSelect
                        value={day.close_time ?? ""}
                        onChange={(v) => updateDay(idx, "close_time", v)}
                        label="Close"
                      />
                    </div>
                    {/* Break */}
                    <div className="flex min-w-[210px] flex-1 flex-wrap items-center gap-2">
                      <span className="shrink-0 text-xs text-muted-foreground">Break:</span>
                      <div className="min-w-[96px] flex-1">
                        <TimeSelect
                          value={day.break_start ?? ""}
                          onChange={(v) => updateDay(idx, "break_start", v || null)}
                          label="Start"
                          placeholder="—"
                        />
                      </div>
                      <span className="shrink-0 text-xs text-muted-foreground">–</span>
                      <div className="min-w-[96px] flex-1">
                        <TimeSelect
                          value={day.break_end ?? ""}
                          onChange={(v) => updateDay(idx, "break_end", v || null)}
                          label="End"
                          placeholder="—"
                        />
                      </div>
                    </div>
                  </>
                )}
                {!day.is_active && (
                  <span className="text-xs text-muted-foreground italic">Closed</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Save */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          onClick={handleSave}
          disabled={saving}
          className="flex w-full sm:w-auto items-center justify-center gap-2 bg-primary text-primary-foreground px-5 py-2.5 rounded-lg text-sm font-semibold hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {saving ? <LoaderPulseSquare className="size-4" /> : <Save className="size-4" />}
          {saved ? "Saved!" : "Save Schedule"}
        </button>
        {saveError && (
          <span className="text-xs text-red-500 flex items-center gap-1">
            <AlertCircle className="size-3" /> {saveError}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Exceptions Tab ─────────────────────────────────────────────────────────────

function ExceptionsTab() {
  const today = new Date();
  const [year, setYear] = useState(today.getFullYear());
  const [month, setMonth] = useState<number | undefined>(today.getMonth() + 1);
  const { data: exceptions, loading, error, refetch } = useExceptions(year, month);

  const [form, setForm] = useState<Partial<ExceptionItem>>({
    exception_date: "",
    exception_type: "CLOSED",
    custom_open: null,
    custom_close: null,
    reason: null,
  });
  const [adding, setAdding] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const handleAdd = async () => {
    if (!form.exception_date) { setFormError("Date is required."); return; }
    if (form.exception_type === "CUSTOM_HOURS" && (!form.custom_open || !form.custom_close)) {
      setFormError("Custom hours require both open and close times.");
      return;
    }
    setFormError(null);
    setAdding(true);
    try {
      await api.post("/dashboard/schedule/exceptions", form);
      setForm({ exception_date: "", exception_type: "CLOSED", custom_open: null, custom_close: null, reason: null });
      await refetch();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Failed to add");
    } finally {
      setAdding(false);
    }
  };

  const handleDelete = async (id: string) => {
    setDeleting(id);
    try {
      await api.del(`/dashboard/schedule/exceptions/${id}`);
      await refetch();
    } finally {
      setDeleting(null);
    }
  };

  return (
    <div className="space-y-6">
      {/* Add form */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-5">
        <h3 className="text-sm font-semibold mb-4 text-muted-foreground uppercase tracking-wider">Add Exception</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 items-end">
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1.5">Date</label>
            <CustomDatePicker
              min={new Date().toISOString().split("T")[0]}
              value={form.exception_date ?? ""}
              onChange={(v) => setForm((p) => ({ ...p, exception_date: v }))}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1.5">Type</label>
            <select
              aria-label="Exception Type"
              value={form.exception_type}
              onChange={(e) => setForm((p) => ({ ...p, exception_type: e.target.value as "CLOSED" | "CUSTOM_HOURS" }))}
              className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="CLOSED">Closed (Holiday)</option>
              <option value="CUSTOM_HOURS">Custom Hours</option>
            </select>
          </div>
          {form.exception_type === "CUSTOM_HOURS" && (
            <>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Open</label>
                <TimeSelect
                  value={form.custom_open ?? ""}
                  onChange={(v) => setForm((p) => ({ ...p, custom_open: v }))}
                  label="Custom Open"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Close</label>
                <TimeSelect
                  value={form.custom_close ?? ""}
                  onChange={(v) => setForm((p) => ({ ...p, custom_close: v }))}
                  label="Custom Close"
                />
              </div>
            </>
          )}
          {form.exception_type === "CLOSED" && (
            <div className="col-span-2">
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Reason (optional)</label>
              <input
                type="text"
                placeholder="e.g. National Holiday"
                value={form.reason ?? ""}
                onChange={(e) => setForm((p) => ({ ...p, reason: e.target.value || null }))}
                className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          )}
        </div>
        {formError && (
          <p className="text-xs text-red-500 mt-2 flex items-center gap-1">
            <AlertCircle className="size-3" /> {formError}
          </p>
        )}
        <button
          onClick={handleAdd}
          disabled={adding}
          className="mt-4 flex w-full sm:w-auto items-center justify-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm font-semibold hover:bg-primary/90 disabled:opacity-50 transition-colors"
        >
          {adding ? <LoaderPulseSquare className="size-4" /> : <Plus className="size-4" />}
          Add Exception
        </button>
      </div>

      {/* Filter */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-muted-foreground">Filter:</span>
        <select
          aria-label="Filter Year"
          value={year}
          onChange={(e) => setYear(Number(e.target.value))}
          className="bg-secondary border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none"
        >
          {[today.getFullYear() - 1, today.getFullYear(), today.getFullYear() + 1].map((y) => (
            <option key={y} value={y}>{y}</option>
          ))}
        </select>
        <select
          aria-label="Filter Month"
          value={month ?? ""}
          onChange={(e) => setMonth(e.target.value ? Number(e.target.value) : undefined)}
          className="bg-secondary border border-border rounded-lg px-3 py-1.5 text-sm focus:outline-none"
        >
          <option value="">All months</option>
          {["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"].map((m, i) => (
            <option key={i} value={i + 1}>{m}</option>
          ))}
        </select>
      </div>

      {/* Exception list */}
      {loading ? (
        <div className="bg-card border border-border rounded-xl p-4 space-y-3 animate-pulse mt-6">
          <div className="h-6 w-1/4 bg-secondary rounded mb-6" />
          <div className="border-b border-border pb-2 mb-2 flex gap-4">
            <div className="h-4 w-1/4 bg-secondary rounded" />
            <div className="h-4 w-1/4 bg-secondary rounded" />
            <div className="h-4 w-auto flex-1 bg-secondary rounded" />
          </div>
          {[...Array(3)].map((_, i) => (
            <div key={i} className="flex gap-4 py-2 border-b border-border/50 last:border-0">
              <div className="h-4 w-1/4 bg-secondary rounded" />
              <div className="h-4 w-1/4 bg-secondary rounded" />
              <div className="h-4 w-auto flex-1 bg-secondary rounded" />
            </div>
          ))}
        </div>
      ) : error ? (
        <ErrorState message={error} />
      ) : !exceptions || exceptions.length === 0 ? (
        <div className="bg-card border border-border rounded-xl p-6 sm:p-10 flex flex-col items-center text-center">
          <CalendarOff className="size-10 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground">No exceptions for this period.</p>
          <p className="text-xs text-muted-foreground/60 mt-1">Add closures or special hours above.</p>
        </div>
      ) : (
        <div className="bg-card border border-border rounded-xl overflow-hidden">
          {/* Scroll inside the card on small screens instead of squishing columns */}
          <div className="overflow-x-auto scrollbar-thin">
          <table className="w-full min-w-[560px] border-collapse">
            <thead>
              <tr>
                {["Date", "Type", "Hours / Reason", ""].map((h) => (
                  <th key={h} className="px-2 sm:px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {exceptions.map((exc) => (
                <tr key={exc.id} className="hover:bg-secondary/50 transition-colors">
                  <td className="px-2 sm:px-4 py-3 text-sm border-b border-border font-medium">{exc.exception_date}</td>
                  <td className="px-2 sm:px-4 py-3 text-sm border-b border-border">
                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                      exc.exception_type === "CLOSED"
                        ? "bg-red-500/10 text-red-500"
                        : "bg-amber-500/10 text-amber-500"
                    }`}>
                      {exc.exception_type === "CLOSED" ? <CalendarOff className="size-3" /> : <Clock className="size-3" />}
                      {exc.exception_type === "CLOSED" ? "Closed" : "Custom Hours"}
                    </span>
                  </td>
                  <td className="px-2 sm:px-4 py-3 text-sm border-b border-border text-muted-foreground">
                    {exc.exception_type === "CUSTOM_HOURS" && exc.custom_open && exc.custom_close
                      ? `${exc.custom_open} – ${exc.custom_close}`
                      : exc.reason ?? "—"}
                  </td>
                  <td className="px-2 sm:px-4 py-3 border-b border-border">
                    <button
                      onClick={() => exc.id && handleDelete(exc.id)}
                      disabled={deleting === exc.id}
                      className="text-muted-foreground hover:text-red-500 transition-colors disabled:opacity-40"
                    >
                      {deleting === exc.id ? <LoaderPulseSquare className="size-4 text-red-500" /> : <Trash2 className="size-4" />}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Preview Tab ────────────────────────────────────────────────────────────────

function PreviewTab() {
  const today = new Date().toISOString().split("T")[0];
  const [startDate, setStartDate] = useState(today);
  const [days, setDays] = useState(7);
  const [triggered, setTriggered] = useState(false);
  const [fetchKey, setFetchKey] = useState(0);

  // Only fetch when user explicitly clicks preview
  const [previewStart, setPreviewStart] = useState(today);
  const [previewDays, setPreviewDays] = useState(7);

  const { data: preview, loading, error } = useSlotPreview(
    triggered ? previewStart : "",
    previewDays,
  );

  const handlePreview = () => {
    setPreviewStart(startDate);
    setPreviewDays(days);
    setTriggered(true);
    setFetchKey((k) => k + 1);
  };

  return (
    <div className="space-y-6">
      <div className="bg-card border border-border rounded-xl p-4 sm:p-5">
        <h3 className="text-sm font-semibold mb-1 text-muted-foreground uppercase tracking-wider">Preview Computed Slots</h3>
        <p className="text-xs text-muted-foreground mb-4">
          See exactly which time slots your schedule will generate for any date range. Great for verifying before going live.
        </p>
        <div className="flex flex-col sm:flex-row sm:items-end gap-3 sm:gap-4">
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1.5">Start Date</label>
            <CustomDatePicker
              min={today}
              value={startDate}
              onChange={(v) => setStartDate(v)}
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1.5">Days to show</label>
            <select
              aria-label="Preview Days"
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="w-full sm:w-auto bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
            >
              {[7, 14, 28].map((d) => (
                <option key={d} value={d}>{d} days</option>
              ))}
            </select>
          </div>
          <button
            onClick={handlePreview}
            className="flex items-center justify-center gap-2 bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm font-semibold hover:bg-primary/90 transition-colors w-full sm:w-auto"
          >
            <Eye className="size-4" /> Preview
          </button>
        </div>
      </div>

      {loading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 animate-pulse mt-6">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="bg-card border border-border rounded-xl p-4 space-y-3">
               <div className="h-4 w-28 bg-secondary rounded" />
               <div className="flex gap-2">
                 <div className="h-5 w-16 bg-secondary rounded-full" />
                 <div className="h-5 w-16 bg-secondary rounded-full" />
                 <div className="h-5 w-16 bg-secondary rounded-full" />
               </div>
            </div>
          ))}
        </div>
      )}
      {error && <ErrorState message={error} />}

      {!loading && triggered && preview && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {preview.map((day) => (
            <div key={day.date} className="bg-card border border-border rounded-xl p-4">
              <div className="text-sm font-semibold mb-2">
                {new Date(day.date + "T00:00:00").toLocaleDateString("en-US", {
                  weekday: "short", month: "short", day: "numeric",
                })}
              </div>
              {day.available_times.length === 0 ? (
                <p className="text-xs text-muted-foreground italic">No slots (closed or fully booked)</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {day.available_times.map((t) => (
                    <span
                      key={t}
                      className="bg-primary/10 text-primary text-[11px] font-semibold px-2 py-0.5 rounded-full"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {!loading && !triggered && (
        <div className="bg-card border border-border rounded-xl p-6 sm:p-10 flex flex-col items-center text-center">
          <Eye className="size-10 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground">Set your date range and click Preview to see computed slots.</p>
        </div>
      )}
    </div>
  );
}

// ── Shared micro-components ────────────────────────────────────────────────────

function parseTime(timeStr: string) {
  if (!timeStr) return { h: 12, m: 0, p: "AM" };
  const [hh, mm] = timeStr.split(":").map(Number);
  const p = hh >= 12 ? "PM" : "AM";
  const h = hh % 12 || 12;
  return { h, m: mm || 0, p };
}

function formatTime(h: number, m: number, p: string) {
  let hh = h;
  if (p === "PM" && h < 12) hh += 12;
  if (p === "AM" && h === 12) hh = 0;
  return `${hh.toString().padStart(2, "0")}:${m.toString().padStart(2, "0")}`;
}

function CustomDatePicker({
  value, onChange, min, placeholder = "Select date"
}: {
  value: string;
  onChange: (v: string) => void;
  min?: string;
  placeholder?: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState({ top: 0, left: 0 });
  
  // Parse incoming value or current date to a local time equivalent
  const getInitialMonth = () => {
    if (!value) return new Date();
    const [y, m, d] = value.split("-").map(Number);
    return new Date(y, m - 1, d);
  };
  const [currentMonth, setCurrentMonth] = useState(getInitialMonth);

  useEffect(() => {
    if (value) {
      const [y, m, d] = value.split("-").map(Number);
      setCurrentMonth(new Date(y, m - 1, d, 0, 0, 0, 0));
    }
  }, [value]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        isOpen &&
        buttonRef.current && !buttonRef.current.contains(event.target as Node) &&
        popupRef.current && !popupRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  useEffect(() => {
    if (isOpen && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      const popupHeight = 320; 
      let top = rect.bottom + window.scrollY + 4;
      if (window.innerHeight - rect.bottom < popupHeight && rect.top > popupHeight) {
         top = rect.top + window.scrollY - popupHeight - 4; 
      }
      let left = rect.left + window.scrollX;
      if (left + 280 > window.innerWidth) {
        left = window.innerWidth - 280 - 16;
      }
      setCoords({ top, left });
      setIsReady(true);
    } else {
      setIsReady(false);
    }
  }, [isOpen]);

  const year = currentMonth.getFullYear();
  const month = currentMonth.getMonth();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstDay = new Date(year, month, 1).getDay();

  const handlePrevMonth = () => setCurrentMonth(new Date(year, month - 1, 1));
  const handleNextMonth = () => setCurrentMonth(new Date(year, month + 1, 1));
  
  let minDate: Date | null = null;
  if (min) {
    const [y, m, d] = min.split("-").map(Number);
    minDate = new Date(y, m - 1, d, 0, 0, 0, 0);
  }

  // Value formatting for display
  let displayValue = "";
  if (value) {
    const [y, m, d] = value.split("-").map(Number);
    displayValue = new Date(y, m - 1, d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }

  return (
    <div className="relative w-full min-w-[140px]">
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between bg-secondary border border-border focus:ring-1 focus:ring-primary h-[38px] px-3 rounded-md text-sm transition-colors hover:bg-secondary/80"
      >
        <span className={displayValue ? "text-foreground font-medium" : "text-muted-foreground"}>
          {displayValue || placeholder}
        </span>
        <Calendar className="w-4 h-4 text-muted-foreground opacity-70" />
      </button>

      {isOpen && isReady && typeof document !== "undefined" && createPortal(
        <div 
          ref={popupRef}
          className="absolute z-[9999] w-[280px] max-w-[calc(100vw-2rem)] bg-card border border-border rounded-xl shadow-lg p-3 font-sans animate-in fade-in zoom-in-95 duration-100 origin-top"
          style={{ top: coords.top, left: coords.left }}
        >
          <div className="flex items-center justify-between mb-2">
            <button type="button" onClick={handlePrevMonth} className="p-1 hover:bg-secondary rounded-md">&lt;</button>
            <div className="text-sm font-semibold">
              {currentMonth.toLocaleDateString("en-US", { month: "long", year: "numeric" })}
            </div>
            <button type="button" onClick={handleNextMonth} className="p-1 hover:bg-secondary rounded-md">&gt;</button>
          </div>
          <div className="grid grid-cols-7 gap-1 text-center mb-1">
            {["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"].map(d => (
              <div key={d} className="text-[10px] font-semibold text-muted-foreground">{d}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-1">
            {Array.from({ length: firstDay }).map((_, i) => <div key={`empty-${i}`} />)}
            {Array.from({ length: daysInMonth }).map((_, i) => {
              const d = i + 1;
              const dateObj = new Date(year, month, d, 0, 0, 0, 0);
              const dateStr = `${year}-${String(month+1).padStart(2,"0")}-${String(d).padStart(2,"0")}`;
              const isDisabled = minDate ? dateObj < minDate : false;
              const isSelected = value === dateStr;
              return (
                <button
                  key={d}
                  type="button"
                  disabled={isDisabled}
                  onClick={() => { onChange(dateStr); setIsOpen(false); }}
                  className={`h-8 rounded-md text-sm flex items-center justify-center transition-colors 
                    ${isDisabled ? "opacity-30 cursor-not-allowed" : "hover:bg-secondary"}
                    ${isSelected ? "bg-primary text-primary-foreground hover:bg-primary/90" : ""}
                  `}
                >
                  {d}
                </button>
              );
            })}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}

function TimeSelect({
  value, onChange, label, placeholder = "Select time"
}: {
  value: string;
  onChange: (v: string) => void;
  label: string;
  placeholder?: string;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [coords, setCoords] = useState({ top: 0, left: 0, width: 0 });
  const { h, m, p } = parseTime(value);

  const [hour, setHour] = useState(h);
  const [minute, setMinute] = useState(m);
  const [period, setPeriod] = useState(p);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        isOpen &&
        buttonRef.current && !buttonRef.current.contains(event.target as Node) &&
        popupRef.current && !popupRef.current.contains(event.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  useEffect(() => {
    if (isOpen && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      const popupHeight = 280; 
      let top = rect.bottom + window.scrollY + 4;
      if (window.innerHeight - rect.bottom < popupHeight && rect.top > popupHeight) {
         top = rect.top + window.scrollY - popupHeight - 4; 
      }
      let left = rect.left + window.scrollX;
      if (left + 256 > window.innerWidth) {
        left = window.innerWidth - 256 - 16;
      }
      setCoords({ top, left, width: Math.max(256, rect.width) });
      setIsReady(true);
    } else {
      setIsReady(false);
    }
  }, [isOpen]);

  useEffect(() => {
    if (value) {
      const parsed = parseTime(value);
      setHour(parsed.h);
      setMinute(parsed.m);
      setPeriod(parsed.p);
    }
  }, [value]);

  const applyTime = (newH: number, newM: number, newP: string) => {
    onChange(formatTime(newH, newM, newP));
  };

  const displayValue = value
    ? `${hour}:${minute.toString().padStart(2, "0")} ${period}`
    : "";

  return (
    // w-full + min-w-0: the parent row/grid decides the width so the picks
    // share space on mobile instead of overflowing with a fixed 7rem each.
    <div className="relative w-full min-w-0" aria-label={label}>
      <button
        ref={buttonRef}
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="w-full flex items-center justify-between gap-1 whitespace-nowrap bg-secondary border border-border focus:ring-1 focus:ring-primary h-[34px] px-2 sm:px-3 rounded-md text-[13px] sm:text-sm transition-colors hover:bg-secondary/80"
      >
        <span className={displayValue ? "text-foreground font-medium" : "text-muted-foreground"}>
          {displayValue || placeholder}
        </span>
        <Clock className="w-3.5 h-3.5 shrink-0 text-muted-foreground opacity-70" />
      </button>

      {isOpen && isReady && typeof document !== "undefined" && createPortal(
        <div 
          ref={popupRef}
          className="absolute z-[9999] w-64 max-w-[calc(100vw-2rem)] bg-card border border-border rounded-xl shadow-lg p-4 font-sans animate-in fade-in zoom-in-95 duration-100 origin-top"
          style={{ top: coords.top, left: coords.left }}
        >
          <div className="flex bg-secondary p-1 rounded-lg mb-4">
            <button
              onClick={() => { setPeriod("AM"); applyTime(hour, minute, "AM"); }}
              className={`flex-1 py-1.5 text-xs font-semibold rounded-md transition-all ${period === "AM" ? "bg-card text-primary shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
            >
              AM
            </button>
            <button
              onClick={() => { setPeriod("PM"); applyTime(hour, minute, "PM"); }}
              className={`flex-1 py-1.5 text-xs font-semibold rounded-md transition-all ${period === "PM" ? "bg-card text-primary shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
            >
              PM
            </button>
          </div>

          <div className="flex gap-4">
            <div className="flex-1">
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest mb-2 text-center">Hour</div>
              <div className="grid grid-cols-3 gap-1">
                {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(hr => (
                  <button
                    key={hr}
                    onClick={() => { setHour(hr); applyTime(hr, minute, period); }}
                    className={`h-8 rounded-md text-sm font-medium transition-all ${hour === hr ? "bg-primary text-primary-foreground" : "bg-secondary text-foreground hover:bg-secondary/80"}`}
                  >
                    {hr}
                  </button>
                ))}
              </div>
            </div>

            <div className="w-16 flex flex-col">
              <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest mb-2 text-center">Minute</div>
              <div className="grid grid-cols-1 gap-1">
                {[0, 15, 30, 45].map(min => (
                  <button
                    key={min}
                    onClick={() => { setMinute(min); applyTime(hour, min, period); setIsOpen(false); }}
                    className={`h-8 rounded-md text-sm font-medium transition-all ${minute === min ? "bg-primary text-primary-foreground" : "bg-secondary text-foreground hover:bg-secondary/80"}`}
                  >
                    :{min.toString().padStart(2, "0")}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="mt-4 pt-4 border-t border-border flex justify-between">
            <button
              onClick={() => { onChange(""); setIsOpen(false); }}
              className="text-xs font-medium text-red-500 hover:text-red-600 transition-colors"
            >
              Clear
            </button>
            <button
              onClick={() => setIsOpen(false)}
              className="text-xs font-semibold text-primary hover:text-primary/80 transition-colors"
            >
              Done
            </button>
          </div>
        </div>,
        document.body
      )}
    </div>
  );
}

function LoadingState({ label }: { label: string }) {
  return (
    <div className="flex flex-col items-center justify-center text-muted-foreground py-16 gap-4">
      <LoaderPulseSquare className="text-primary w-8 h-8" />
      <span className="text-sm font-medium animate-pulse">{label}</span>
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 text-red-500 py-4">
      <AlertCircle className="size-4" /> {message}
    </div>
  );
}

// ── Page entry point ───────────────────────────────────────────────────────────

type Tab = "schedule" | "exceptions" | "preview";
type TabDef = { id: Tab; label: string; icon: ReactNode };

const TABS: TabDef[] = [
  { id: "schedule", label: "Weekly Schedule", icon: <Calendar className="size-4" /> },
  { id: "exceptions", label: "Exceptions & Closures", icon: <CalendarOff className="size-4" /> },
  { id: "preview", label: "Preview", icon: <Eye className="size-4" /> },
];

export default function AvailabilityPage() {
  const [tab, setTab] = useState<Tab>("schedule");
  const [isOrg, setIsOrg] = useState(false);
  useEffect(() => {
    import("@/lib/org-api").then(({ orgApi }) =>
      orgApi.scope().then((s) => setIsOrg(s.org_type === "organization")).catch(() => {})
    ).catch(() => {});
  }, []);
  const [indicatorStyle, setIndicatorStyle] = useState({ left: 4, width: 0, opacity: 0 });
  const tabsRef = useRef<(HTMLButtonElement | null)[]>([]);

  // Measure the active tab button to perfectly position the sliding background indicator
  useEffect(() => {
    const activeIndex = TABS.findIndex((t) => t.id === tab);
    const btn = tabsRef.current[activeIndex];
    if (btn) {
      setIndicatorStyle({
        left: btn.offsetLeft,
        width: btn.offsetWidth,
        opacity: 1
      });
    }
  }, [tab]);

  return (
    <>
      <div className="flex items-end justify-between mb-6">
        <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">Business Hours</h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1">
            Define your weekly schedule once — the booking engine handles everything else automatically.
          </p>
        </div>
      </div>

      {isOrg && (
        <div className="mb-6 border border-primary/30 bg-primary/5 rounded-xl p-4 text-sm">
          <p className="font-semibold">🏥 Organization default hours</p>
          <p className="text-muted-foreground text-xs mt-1">
            This is the fallback used when a team member has no custom time-table. Manage each
            member&apos;s own hours, timezone and duration in{" "}
            <a href="/dashboard/departments" className="text-primary hover:underline font-medium">
              Departments → open a department → Hours
            </a>
            .
          </p>
        </div>
      )}

      {/* Tab bar with sliding indicator */}
      <div className="relative flex items-center gap-1 bg-secondary/50 p-1 rounded-xl mb-6 w-fit max-w-full overflow-x-auto border border-border">
        {/* The sliding background pill */}
        <div 
          className="absolute top-1 bottom-1 bg-card rounded-lg shadow-sm border border-border/50 transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]"
          style={{ 
            left: `${indicatorStyle.left}px`, 
            width: `${indicatorStyle.width}px`, 
            opacity: indicatorStyle.opacity 
          }}
        />
        
        {TABS.map((t, index) => (
          <button
            key={t.id}
            ref={(el) => { tabsRef.current[index] = el; }}
            onClick={() => setTab(t.id)}
            className={`relative z-10 flex shrink-0 items-center gap-2 whitespace-nowrap px-3 sm:px-4 py-2 rounded-lg text-[13px] sm:text-sm font-medium transition-colors duration-300 ${
              tab === t.id
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content wrapper utilizing React keys to force re-mounting and trigger the CSS animation */}
      <div key={tab} className="animate-in fade-in slide-in-from-bottom-4 duration-500 ease-out fill-mode-both">
        {tab === "schedule" && <WeeklyScheduleTab />}
        {tab === "exceptions" && <ExceptionsTab />}
        {tab === "preview" && <PreviewTab />}
      </div>
    </>
  );
}
