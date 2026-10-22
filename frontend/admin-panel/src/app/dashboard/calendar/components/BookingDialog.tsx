"use client";

import { X, User, Mail, Phone, Clock, Calendar as CalendarIcon, Info, ShieldCheck, Video } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface CalendarEvent {
  id: string;
  customer_name: string;
  customer_email: string;
  customer_phone: string | null;
  slot_start: string | null;
  slot_end: string | null;
  created_at: string;
  collected_data: Record<string, unknown>;
  meeting_type_snapshot?: string | null;
  meeting_link_status?: string | null;
  meeting_link_url?: string | null;
  meeting_link_provider?: string | null;
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
}

function formatFieldLabel(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function BookingDialog({ 
  event, 
  onClose 
}: { 
  event: CalendarEvent | null; 
  onClose: () => void;
}) {
  if (!event) return null;

  const coreFields = [
    { icon: User, label: "Customer", value: event.customer_name },
    { icon: Mail, label: "Email Address", value: event.customer_email },
    { icon: Phone, label: "Phone Number", value: event.customer_phone ?? "Not provided" },
  ];

  const INTERNAL_KEYS = new Set([
    "user_timezone",
    "locked_slot_id",
    "locked_slot_start_utc",
    "locked_slot_end_utc",
    "timezone_source",
    "timezone_fallback_used",
    "timezone_fallback_reason",
    "selected_slot_id",
    "selected_slot_start_utc",
    "runtime_checkpoint",
    "active_path_effects",
    "active_path",
    "checkpoint",
    "field_definitions_snapshot",
    "field_definitions",
    "field_snapshot",
    "slot_id",
  ]);

  const extraEntries = Object.entries(event.collected_data).filter(([k, v]) => {
    const norm = k.toLowerCase().replace(/ /g, "_");
    if (["name", "email", "phone", "date", "time", "appointment_date", "appointment_time"].includes(norm)) return false;
    if (INTERNAL_KEYS.has(norm)) return false;
    if (/(checkpoint|path_effects|snapshot|slot_id|timezone|locked_slot)/i.test(k)) return false;
    if (v === null || v === undefined || v === "") return false;
    // Hide internal objects/arrays — important booking fields are primitives
    if (typeof v === "object") return false;
    return true;
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 animate-in fade-in duration-200">
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-background/80 backdrop-blur-sm" 
        onClick={onClose}
      />

      {/* Modal Content */}
      <div className="relative w-full max-w-xl bg-card border border-border rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300">
        {/* Header Section */}
        <div className="relative h-28 sm:h-32 bg-gradient-to-br from-primary/20 via-primary/5 to-transparent p-4 sm:p-6 flex flex-col justify-end">
          <button 
            onClick={onClose}
            title="Close dialog"
            className="absolute top-4 right-4 p-2 rounded-full hover:bg-background/50 text-muted-foreground hover:text-foreground transition-all"
          >
            <X className="size-5" />
          </button>
          
          <div className="flex items-center gap-3 sm:gap-4">
            <div className="size-12 sm:size-16 rounded-xl bg-primary text-primary-foreground flex items-center justify-center text-xl sm:text-2xl font-bold shadow-lg shadow-primary/20">
              {event.customer_name.split(" ").map(n => n[0]).join("").toUpperCase().slice(0, 2)}
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <Badge className="bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 border-emerald-500/20 px-2 py-0 h-5 text-[10px] font-semibold uppercase tracking-wider">
                  Confirmed
                </Badge>
                <span className="text-[10px] text-muted-foreground font-semibold tracking-widest opacity-50">#{event.id.slice(-8)}</span>
              </div>
              <h2 className="text-xl sm:text-2xl font-bold tracking-tight leading-tight">{event.customer_name}</h2>
            </div>
          </div>
        </div>

        {/* Body Section */}
        <div className="p-4 sm:p-6 space-y-6 sm:space-y-8 max-h-[70vh] overflow-y-auto custom-scrollbar">
          {/* Time & Date Card */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="bg-secondary/30 rounded-xl p-4 border border-border/50">
              <div className="flex items-center gap-2 text-muted-foreground mb-1">
                <CalendarIcon className="size-3.5" />
                <span className="text-[10px] font-semibold uppercase tracking-wider">Date</span>
              </div>
              <div className="text-sm font-semibold">
                {event.slot_start ? new Date(event.slot_start).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" }) : "—"}
              </div>
            </div>
            <div className="bg-secondary/30 rounded-xl p-4 border border-border/50">
              <div className="flex items-center gap-2 text-muted-foreground mb-1">
                <Clock className="size-3.5" />
                <span className="text-[10px] font-semibold uppercase tracking-wider">Time</span>
              </div>
              <div className="text-sm font-semibold">
                {event.slot_start ? fmtTime(event.slot_start) : "—"}
              </div>
            </div>
          </div>

          {/* Booking Mode */}
          <div className="space-y-2">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground px-1">Booking Mode</h3>
            <div className={`rounded-xl p-4 border flex items-center justify-between ${event.meeting_type_snapshot === "ONLINE" ? "bg-blue-500/10 border-blue-500/20" : "bg-secondary/30 border-border/50"}`}>
              <div className="flex items-center gap-3">
                <div className={`size-10 rounded-xl flex items-center justify-center shrink-0 ${event.meeting_type_snapshot === "ONLINE" ? "bg-blue-500 text-white" : "bg-muted text-muted-foreground"}`}>
                  {event.meeting_type_snapshot === "ONLINE" ? <Video className="size-5" /> : <CalendarIcon className="size-5" />}
                </div>
                <div>
                  <div className="text-sm font-semibold">{event.meeting_type_snapshot === "ONLINE" ? "Online" : "Offline"}</div>
                  <div className="text-xs text-muted-foreground">
                    {event.meeting_type_snapshot === "ONLINE" ? (event.meeting_link_provider || "Meeting Provider") : "In-person"}
                  </div>
                </div>
              </div>
              {event.meeting_type_snapshot === "ONLINE" && event.meeting_link_status === "READY" && event.meeting_link_url && (
                <a href={event.meeting_link_url} target="_blank" rel="noopener noreferrer" className="text-xs font-semibold px-3 py-1.5 rounded-full bg-blue-500 text-white hover:bg-blue-600 transition-colors">
                  Join Link
                </a>
              )}
            </div>
            {event.meeting_type_snapshot === "ONLINE" && event.meeting_link_status === "UNAVAILABLE" && (
              <p className="text-xs text-amber-600 dark:text-amber-400 px-1">Meeting link temporarily unavailable. Please retry.</p>
            )}
            {event.meeting_type_snapshot === "ONLINE" && event.meeting_link_status === "FAILED" && (
              <p className="text-xs text-amber-600 dark:text-amber-400 px-1">The meeting link could not be created. Please add it manually or contact support.</p>
            )}
            {event.meeting_type_snapshot === "ONLINE" && event.meeting_link_url && (
              <p className="text-xs text-muted-foreground px-1 break-all">Link: <a href={event.meeting_link_url} target="_blank" rel="noopener noreferrer" className="text-blue-500 hover:underline">{event.meeting_link_url}</a></p>
            )}
          </div>

          {/* Appointment Status - Online vs Offline differentiated */}
          <div className="space-y-3">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground px-1">Appointment Status • {event.meeting_type_snapshot === "ONLINE" ? "Online" : "Offline"}</h3>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {[
                { label: "Attended", value: "ATTENDED", color: "bg-green-500 hover:bg-green-600 text-white", icon: "✓" },
                { label: "No-Show", value: "NO_SHOW", color: "bg-red-500 hover:bg-red-600 text-white", icon: "✕" },
                { label: "Cancelled", value: "CANCELLED", color: "bg-zinc-500 hover:bg-zinc-600 text-white", icon: "⊘" },
                { label: "Rescheduled", value: "RESCHEDULED", color: "bg-blue-500 hover:bg-blue-600 text-white", icon: "↻" },
              ].map((opt) => (
                <button
                  key={opt.value}
                  onClick={async () => {
                    try {
                      const { api } = await import("@/lib/api");
                      await api.put(`/dashboard/bookings/${event.id}/attendance`, { attendance_status: opt.value });
                      // Simple feedback - reload or toast
                      const { toast } = await import("sonner");
                      toast.success(`Marked as ${opt.label} (${event.meeting_type_snapshot === "ONLINE" ? "Online: link click will auto-verify" : "Offline: manual after appointment"})`);
                      // Optimistically update UI
                      (event as unknown as Record<string, unknown>).attendance_status = opt.value;
                    } catch (e) {
                      const { toast } = await import("sonner");
                      toast.error(e instanceof Error ? e.message : "Failed to update");
                    }
                  }}
                  className={`text-xs font-semibold px-3 py-2 rounded-lg border transition-colors ${opt.color} border-transparent`}
                  title={event.meeting_type_snapshot === "ONLINE" ? `Online: ${opt.label} - ${opt.value === "ATTENDED" ? "auto if Join clicked" : "manual"}` : `Offline: ${opt.label} - manual`}
                >
                  <span className="mr-1">{opt.icon}</span> {opt.label}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-muted-foreground px-1">
              {event.meeting_type_snapshot === "ONLINE" ? "Online: Attended auto-marks when Join is clicked; otherwise manual. Offline: Admin marks after appointment." : "Offline: Admin marks attendance after the in-person appointment."}
            </p>
          </div>

          {/* Core Info */}
          <div className="space-y-4">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground px-1">Customer Info</h3>
            <div className="grid gap-3">
              {coreFields.map(({ icon: Icon, label, value }) => (
                <div key={label} className="flex items-center gap-4 p-4 rounded-xl bg-card border border-border hover:border-primary/20 transition-colors">
                  <div className="size-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center shrink-0">
                    <Icon className="size-4" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider mb-0.5">{label}</div>
                    <div className="text-sm font-semibold truncate">{value}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Collected Data */}
          {extraEntries.length > 0 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 px-1">
                <Info className="size-3.5 text-muted-foreground" />
                <h3 className="text-[11px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">Additional Data</h3>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {extraEntries.map(([key, val]) => (
                  <div key={key} className="p-4 rounded-xl bg-muted/20 border border-border/10 flex flex-col gap-1">
                    <span className="text-[9px] font-semibold text-muted-foreground uppercase tracking-tighter">{formatFieldLabel(key)}</span>
                    <span className="text-[13px] font-medium leading-normal italic text-foreground/80">
                      {val === null ? "—" : String(val)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 sm:p-6 border-t border-border bg-muted/5 flex items-center justify-between">
          <div className="flex items-center gap-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-widest">
            <ShieldCheck className="size-3 text-emerald-500" />
            Verified Booking
          </div>
          <Button 
            onClick={onClose}
            title="Close details"
            className="rounded-xl px-8 h-10 font-bold tracking-tight shadow-lg shadow-primary/20"
          >
            Close
          </Button>
        </div>
      </div>
    </div>
  );
}
