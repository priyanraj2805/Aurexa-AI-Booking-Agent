"use client";

import { 
  Clock, 
  Calendar as CalendarIcon, 
  ChevronRight, 
  RefreshCcw, 
  AlertCircle,
  Loader2
} from "lucide-react";
import { Badge } from "@/components/ui/badge";

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

export function AgendaView({ 
  items, 
  loading, 
  error, 
  onRefresh, 
  onSelectEvent,
  hasMore,
  onLoadMore
}: { 
  items: CalendarEvent[]; 
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onSelectEvent: (e: CalendarEvent) => void;
  hasMore: boolean;
  onLoadMore: () => void;
}) {
  // Group by date
  const groups: Record<string, CalendarEvent[]> = {};
  items.forEach(ev => {
    if (!ev.slot_start) return;
    const date = new Date(ev.slot_start).toLocaleDateString("en-US", { 
      weekday: "long", 
      month: "long", 
      day: "numeric",
      year: "numeric"
    });
    if (!groups[date]) groups[date] = [];
    groups[date].push(ev);
  });

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex items-center justify-between gap-2 px-2">
        <div className="flex min-w-0 items-center gap-3">
          <div className="size-10 shrink-0 rounded-xl bg-primary/10 text-primary flex items-center justify-center">
            <Clock className="size-5" />
          </div>
          <div className="min-w-0">
            <h2 className="text-xl font-bold tracking-tight">Upcoming Agenda</h2>
            <p className="text-xs text-muted-foreground font-semibold uppercase tracking-widest">
              Live schedule feed
            </p>
          </div>
        </div>
        <button
          onClick={onRefresh}
          disabled={loading}
          title="Refresh agenda"
          className="flex shrink-0 items-center gap-2 px-3 sm:px-4 py-2 rounded-xl bg-secondary hover:bg-accent text-sm font-semibold transition-all disabled:opacity-50"
        >
          <RefreshCcw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          <span className="hidden min-[400px]:inline">Refresh</span>
        </button>
      </div>

      {error && (
        <div className="p-12 text-center bg-destructive/5 rounded-2xl border border-destructive/10">
          <AlertCircle className="size-12 text-destructive mx-auto mb-4 opacity-50" />
          <p className="text-sm font-bold text-destructive">{error}</p>
          <button onClick={onRefresh} className="mt-4 text-xs font-semibold uppercase tracking-widest underline">Try Again</button>
        </div>
      )}

      {items.length === 0 && !loading && !error && (
        <div className="p-12 sm:p-24 text-center bg-muted/20 rounded-2xl border border-dashed border-border/50">
          <CalendarIcon className="size-16 text-muted-foreground/20 mx-auto mb-4" />
          <h3 className="text-lg font-bold">No upcoming bookings</h3>
          <p className="text-sm text-muted-foreground mt-1">You&apos;re all caught up for now!</p>
        </div>
      )}

      <div className="space-y-12">
        {Object.entries(groups).map(([date, dayEvents]) => (
          <div key={date} className="space-y-4">
            <div className="sticky top-0 z-10 py-2 bg-background/80 backdrop-blur-md">
              <h3 className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground px-4 flex items-center gap-3">
                {date}
                <div className="h-px flex-1 bg-border/50" />
              </h3>
            </div>
            
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 px-2">
              {dayEvents.map((ev) => (
                <button
                  key={ev.id}
                  onClick={() => onSelectEvent(ev)}
                  className="group relative flex flex-col gap-4 p-5 text-left bg-card border border-border rounded-2xl hover:border-primary/40 hover:shadow-lg transition-all outline-none"
                >
                  <div className="flex items-start justify-between">
                    <div className="size-12 rounded-xl bg-primary/10 text-primary flex items-center justify-center text-sm font-bold group-hover:scale-110 transition-transform">
                      {ev.customer_name.split(" ").map(n => n[0]).join("").toUpperCase().slice(0, 2)}
                    </div>
                    <div className="text-right">
                      <div className="text-lg font-bold tracking-tighter text-primary">
                        {ev.slot_start ? fmtTime(ev.slot_start) : "—"}
                      </div>
                      <Badge variant="secondary" className="text-[9px] font-semibold uppercase tracking-tighter h-5 px-2">
                        Confirmed
                      </Badge>
                    </div>
                  </div>
                  
                  <div className="space-y-1">
                    <div className="text-sm font-semibold truncate group-hover:text-primary transition-colors">
                      {ev.customer_name}
                    </div>
                    <div className="text-[11px] text-muted-foreground font-medium truncate opacity-70">
                      {ev.customer_email}
                    </div>
                  </div>

                  <div className="pt-3 border-t border-border/40 flex items-center justify-between text-[10px] font-semibold text-muted-foreground uppercase tracking-widest">
                    <span>Quick View</span>
                    <ChevronRight className="size-3 group-hover:translate-x-1 transition-transform" />
                  </div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {hasMore && (
        <div className="py-12 flex flex-col items-center gap-4">
          <button
            onClick={onLoadMore}
            disabled={loading}
            title="Load more booking items"
            className="px-8 py-3 rounded-xl bg-primary/5 border border-primary/10 text-primary text-sm font-bold hover:bg-primary/10 transition-all flex items-center gap-2"
          >
            {loading ? <Loader2 className="size-4 animate-spin" /> : "Load More Bookings"}
          </button>
        </div>
      )}
    </div>
  );
}
