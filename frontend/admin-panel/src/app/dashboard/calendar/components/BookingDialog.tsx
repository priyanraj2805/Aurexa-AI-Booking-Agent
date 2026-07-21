"use client";

import { X, User, Mail, Phone, Clock, Calendar as CalendarIcon, Info, ShieldCheck } from "lucide-react";
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

  const extraEntries = Object.entries(event.collected_data).filter(
    ([k]) => !["name", "email", "phone", "date", "time", "appointment_date", "appointment_time"].includes(k)
  );

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
        <div className="relative h-32 bg-gradient-to-br from-primary/20 via-primary/5 to-transparent p-6 flex flex-col justify-end">
          <button 
            onClick={onClose}
            title="Close dialog"
            className="absolute top-4 right-4 p-2 rounded-full hover:bg-background/50 text-muted-foreground hover:text-foreground transition-all"
          >
            <X className="size-5" />
          </button>
          
          <div className="flex items-center gap-4">
            <div className="size-16 rounded-xl bg-primary text-primary-foreground flex items-center justify-center text-2xl font-bold shadow-lg shadow-primary/20">
              {event.customer_name.split(" ").map(n => n[0]).join("").toUpperCase().slice(0, 2)}
            </div>
            <div>
              <div className="flex items-center gap-2 mb-1">
                <Badge className="bg-emerald-500/10 text-emerald-500 hover:bg-emerald-500/20 border-emerald-500/20 px-2 py-0 h-5 text-[10px] font-semibold uppercase tracking-wider">
                  Confirmed
                </Badge>
                <span className="text-[10px] text-muted-foreground font-semibold tracking-widest opacity-50">#{event.id.slice(-8)}</span>
              </div>
              <h2 className="text-2xl font-bold tracking-tight leading-tight">{event.customer_name}</h2>
            </div>
          </div>
        </div>

        {/* Body Section */}
        <div className="p-6 space-y-8 max-h-[70vh] overflow-y-auto custom-scrollbar">
          {/* Time & Date Card */}
          <div className="grid grid-cols-2 gap-4">
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
        <div className="p-6 border-t border-border bg-muted/5 flex items-center justify-between">
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
