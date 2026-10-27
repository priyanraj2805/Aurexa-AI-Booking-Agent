"use client";

import { useEffect, useState } from "react";
import { orgApi, Provider, ProviderException, ProviderPreviewDay } from "@/lib/org-api";
import { TimezonePicker } from "@/components/timezone-picker";
import { TimeSelect } from "@/components/time-select";
import { toast } from "sonner";

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const DURATIONS = [15, 20, 30, 45, 60, 90, 120];

interface DayRow {
  day_of_week: number;
  is_active: boolean;
  open_time?: string | null;
  close_time?: string | null;
  break_start?: string | null;
  break_end?: string | null;
}

// Same content as Business Hours, scoped to one doctor:
// Weekly (toggle + open/close + break + duration + horizon + timezone),
// Off days (date + CLOSED/CUSTOM_HOURS + reason), Preview (date → slots).

export function DoctorHoursEditor({ provider, onSaved }: { provider: Provider; onSaved: () => void }) {
  const [tab, setTab] = useState<"weekly" | "exceptions" | "preview">("weekly");
  const [days, setDays] = useState<DayRow[]>([]);
  const [breakOpenDays, setBreakOpenDays] = useState<number[]>([]);
  const [tz, setTz] = useState(provider.timezone || "");
  const [dur, setDur] = useState(provider.slot_duration_minutes ? String(provider.slot_duration_minutes) : "");
  const [horizon, setHorizon] = useState(provider.booking_window_days ? String(provider.booking_window_days) : "");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const rows = await orgApi.providerSchedule(provider.id);
        const schedule = [0, 1, 2, 3, 4, 5, 6].map((dow) => {
          const hit = rows.find((d) => d.day_of_week === dow);
          return hit
            ? {
                day_of_week: dow,
                is_active: hit.is_active,
                open_time: hit.open_time,
                close_time: hit.close_time,
                break_start: hit.break_start,
                break_end: hit.break_end,
              }
            : { day_of_week: dow, is_active: false, open_time: null, close_time: null, break_start: null, break_end: null };
        });
        setDays(schedule);
        setBreakOpenDays(
          schedule
            .filter((day) => day.break_start || day.break_end)
            .map((day) => day.day_of_week)
        );
      } catch (e) {
        toast.error(e instanceof Error ? e.message : "Failed to load hours");
      } finally {
        setLoading(false);
      }
    })();
  }, [provider.id]);

  const updateDay = (idx: number, field: keyof DayRow, value: string | boolean | null) => {
    setDays((prev) =>
      prev.map((d, i) => {
        if (i !== idx) return d;
        const next = { ...d, [field]: value } as DayRow;
        if (field === "is_active" && value === true) {
          if (!next.open_time) next.open_time = "10:00";
          if (!next.close_time) next.close_time = "14:00";
        }
        return next;
      })
    );
  };

  const saveWeekly = async () => {
    setSaving(true);
    try {
      await orgApi.updateProvider(provider.id, {
        timezone: tz.trim() || null,
        slot_duration_minutes: dur ? Number(dur) : null,
        booking_window_days: horizon ? Number(horizon) : null,
      } as Partial<Provider>);
      await orgApi.saveProviderSchedule(provider.id, days);
      toast.success(`Hours saved for ${provider.full_name}`);
      onSaved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <p className="text-sm text-muted-foreground">Loading hours…</p>;

  return (
    <div className="mt-3 pt-3 border-t border-border space-y-4">
      {/* Tabs mirror Business Hours */}
      <div className="flex gap-1 bg-secondary/50 p-1 rounded-lg w-fit border border-border">
        {(["weekly", "exceptions", "preview"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-3 py-1.5 rounded-md text-xs font-semibold capitalize transition-colors ${
              tab === t ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t === "weekly" ? "Weekly" : t === "exceptions" ? "Off days" : "Preview"}
          </button>
        ))}
      </div>

      {tab === "weekly" && (
        <div className="space-y-4">
          <details className="rounded-lg border border-border bg-background px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium">Advanced scheduling settings</summary>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-3">
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Slot duration</label>
                <select
                  value={dur}
                  onChange={(e) => setDur(e.target.value)}
                  className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="">Organization default</option>
                  {DURATIONS.map((d) => (
                    <option key={d} value={d}>{d} min</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Booking horizon</label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    max={366}
                    value={horizon}
                    placeholder="Default"
                    onChange={(e) => setHorizon(e.target.value)}
                    className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
                  />
                  <span className="text-xs text-muted-foreground whitespace-nowrap">days</span>
                </div>
              </div>
              <div>
                <label className="block text-xs font-medium text-muted-foreground mb-1.5">Timezone</label>
                <TimezonePicker value={tz} onChange={setTz} />
              </div>
            </div>
          </details>

          <div className="border border-border rounded-xl overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <h3 className="text-sm font-semibold">Weekly availability</h3>
              <p className="mt-1 text-xs text-muted-foreground">Choose the days and times this member is available.</p>
            </div>
            <div className="divide-y divide-border">
              {days.map((day, idx) => (
                <div key={day.day_of_week} className={`grid min-w-0 gap-3 px-3 py-3 sm:px-4 sm:grid-cols-[8rem_minmax(0,1fr)] sm:items-center transition-colors ${day.is_active ? "" : "opacity-60"}`}>
                  <div className="flex min-w-0 items-center gap-3">
                    <button
                      type="button"
                      aria-label={day.is_active ? `Deactivate ${DAY_NAMES[day.day_of_week]}` : `Activate ${DAY_NAMES[day.day_of_week]}`}
                      onClick={() => updateDay(idx, "is_active", !day.is_active)}
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none ${
                        day.is_active ? "bg-primary" : "bg-secondary"
                      }`}
                    >
                      <span className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transition-transform ${day.is_active ? "translate-x-4" : "translate-x-0"}`} />
                    </button>
                    <span className="truncate text-sm font-medium">{DAY_NAMES[day.day_of_week]}</span>
                  </div>
                  {day.is_active ? (
                    <div className="grid min-w-0 gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
                      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2">
                        <TimeSelect value={day.open_time ?? ""} onChange={(v) => updateDay(idx, "open_time", v)} label="Open" />
                        <span className="text-sm text-muted-foreground shrink-0">–</span>
                        <TimeSelect value={day.close_time ?? ""} onChange={(v) => updateDay(idx, "close_time", v)} label="Close" />
                      </div>
                      <button
                        type="button"
                        aria-expanded={breakOpenDays.includes(day.day_of_week)}
                        onClick={() => setBreakOpenDays((current) =>
                          current.includes(day.day_of_week)
                            ? current.filter((dow) => dow !== day.day_of_week)
                            : [...current, day.day_of_week]
                        )}
                        className="justify-self-start text-xs font-medium text-primary hover:underline sm:justify-self-end"
                      >
                        {breakOpenDays.includes(day.day_of_week) ? "Hide break" : day.break_start || day.break_end ? "Edit break" : "Add break"}
                      </button>
                      {breakOpenDays.includes(day.day_of_week) && (
                        <div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 sm:col-span-2 sm:max-w-md">
                          <span className="text-xs text-muted-foreground">Break</span>
                          <TimeSelect value={day.break_start ?? ""} onChange={(v) => updateDay(idx, "break_start", v || null)} label="Break start" placeholder="Start" />
                          <span className="text-xs text-muted-foreground">–</span>
                          <TimeSelect value={day.break_end ?? ""} onChange={(v) => updateDay(idx, "break_end", v || null)} label="Break end" placeholder="End" />
                        </div>
                      )}
                    </div>
                  ) : (
                    <span className="text-xs text-muted-foreground">Closed</span>
                  )}
                </div>
              ))}
            </div>
          </div>

          <button
            type="button"
            onClick={saveWeekly}
            disabled={saving}
            className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save hours"}
          </button>
        </div>
      )}

      {tab === "exceptions" && <DoctorExceptions providerId={provider.id} />}
      {tab === "preview" && <DoctorPreview providerId={provider.id} />}
    </div>
  );
}

function DoctorExceptions({ providerId }: { providerId: string }) {
  const [list, setList] = useState<ProviderException[]>([]);
  const [date, setDate] = useState("");
  const [type, setType] = useState("CLOSED");
  const [open, setOpen] = useState("");
  const [close, setClose] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    try {
      setList(await orgApi.providerExceptions(providerId));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load off-days");
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [providerId]);

  const add = async () => {
    if (!date) {
      toast.error("Date is required");
      return;
    }
    if (type === "CUSTOM_HOURS" && (!open || !close)) {
      toast.error("Custom hours need open + close");
      return;
    }
    setBusy(true);
    try {
      await orgApi.createProviderException(providerId, {
        exception_date: date,
        exception_type: type,
        custom_open: open || null,
        custom_close: close || null,
        reason: reason || null,
      });
      setDate("");
      setOpen("");
      setClose("");
      setReason("");
      toast.success("Off-day added");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Add failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Date</label>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Type</label>
          <select value={type} onChange={(e) => setType(e.target.value)} className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary">
            <option value="CLOSED">Closed (Holiday)</option>
            <option value="CUSTOM_HOURS">Custom Hours</option>
          </select>
        </div>
        {type === "CUSTOM_HOURS" && (
          <>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Open</label>
              <TimeSelect value={open} onChange={setOpen} label="Custom Open" />
            </div>
            <div>
              <label className="block text-xs font-medium text-muted-foreground mb-1.5">Close</label>
              <TimeSelect value={close} onChange={setClose} label="Custom Close" />
            </div>
          </>
        )}
        <div className={type === "CUSTOM_HOURS" ? "sm:col-span-2" : ""}>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Reason (optional)</label>
          <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Festival" className="w-full bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
        </div>
      </div>
      <button onClick={add} disabled={busy} className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold disabled:opacity-50">
        {busy ? "Adding…" : "Add Off-day"}
      </button>
      <div className="grid gap-2">
        {list.map((x) => (
          <div key={x.id} className="flex items-center justify-between border border-border rounded-lg p-3 text-sm">
            <div>
              <p className="font-medium">{x.exception_date} · {x.exception_type === "CLOSED" ? "Closed" : `${x.custom_open}–${x.custom_close}`}</p>
              {x.reason && <p className="text-xs text-muted-foreground">{x.reason}</p>}
            </div>
            <button
              onClick={async () => {
                try {
                  await orgApi.deleteProviderException(providerId, x.id);
                  toast.success("Deleted");
                  load();
                } catch (e) {
                  toast.error(e instanceof Error ? e.message : "Delete failed");
                }
              }}
              className="text-xs text-destructive hover:underline"
            >
              Delete
            </button>
          </div>
        ))}
        {!list.length && <p className="text-sm text-muted-foreground">No off-days for this member.</p>}
      </div>
    </div>
  );
}

function DoctorPreview({ providerId }: { providerId: string }) {
  const today = new Date().toISOString().split("T")[0];
  const [date, setDate] = useState(today);
  const [days, setDays] = useState(7);
  const [result, setResult] = useState<ProviderPreviewDay[] | null>(null);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    if (!date) return;
    setLoading(true);
    try {
      setResult(await orgApi.previewProviderSchedule(providerId, date, days));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Preview failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-col sm:flex-row sm:items-end gap-3">
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Start Date</label>
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)} className="bg-secondary border border-border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary" />
        </div>
        <div>
          <label className="block text-xs font-medium text-muted-foreground mb-1.5">Days to show</label>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="w-[70px] bg-secondary border border-border rounded-sm px-1 py-0.5 text-[9px] focus:outline-none focus:ring-1 focus:ring-primary">
            {[7, 14, 28].map((d) => (
              <option key={d} value={d}>{d}d</option>
            ))}
          </select>
        </div>
        <button onClick={run} disabled={loading} className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold disabled:opacity-50">
          {loading ? "Loading…" : "Preview"}
        </button>
      </div>
      {result && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {result.map((d) => (
            <div key={d.date} className="border border-border rounded-lg p-3">
              <p className="text-sm font-semibold mb-1">{d.date}</p>
              {!d.available_times.length ? (
                <p className="text-xs text-muted-foreground italic">No slots (closed or fully booked)</p>
              ) : (
                <div className="flex flex-wrap gap-1.5">
                  {d.available_times.map((t) => (
                    <span key={t} className="bg-primary/10 text-primary text-[11px] font-semibold px-2 py-0.5 rounded-full">
                      {t}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
