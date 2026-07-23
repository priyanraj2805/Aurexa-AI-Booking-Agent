"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { 
  Loader2, 
  AlertCircle, 
  ChevronLeft, 
  ChevronRight, 
  Calendar as CalendarIcon, 
  LayoutGrid,
  Clock,
  IterationCcw,
  Plus
} from "lucide-react";
import { api } from "@/lib/api";
import { LoaderPulseSquare } from "@/components/ui/loaders";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { BookingDialog } from "./components/BookingDialog";
import { AgendaView } from "./components/AgendaView";
import { Card, CardContent } from "@/components/ui/card";

// ── Types ──────────────────────────────────────────────────────────────────────

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

// ── Helpers ────────────────────────────────────────────────────────────────────

function pad(n: number) { return String(n).padStart(2, "0"); }

function toLocalDateStr(iso: string): string {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function getMonthDays(year: number, month: number): (number | null)[] {
  const firstDay = new Date(year, month, 1);
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const startOffset = firstDay.getDay(); // 0=Sun
  return [
    ...Array<null>(startOffset).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
}

// ── Day Events Centered Dialog ────────────────────────────────────────────────

function DayEventsDialog({ 
  dateStr, 
  events, 
  onClose, 
  onSelectEvent 
}: { 
  dateStr: string; 
  events: CalendarEvent[]; 
  onClose: () => void;
  onSelectEvent: (ev: CalendarEvent) => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="absolute inset-0 bg-background/80 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-lg bg-card border border-border rounded-3xl shadow-2xl p-6 sm:p-8 animate-in zoom-in-95 duration-300">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h2 className="text-xl sm:text-2xl font-bold tracking-tight">{dateStr}</h2>
            <p className="text-xs text-muted-foreground font-semibold uppercase tracking-widest mt-1">
              Daily Schedule • {events.length} Bookings
            </p>
          </div>
          <button 
            onClick={onClose} 
            title="Close dialog"
            className="p-2 rounded-xl hover:bg-muted text-muted-foreground hover:text-foreground transition-all"
          >
            <Plus className="size-5 rotate-45" />
          </button>
        </div>

        <div className="space-y-3 max-h-[50vh] overflow-y-auto custom-scrollbar pr-2">
          {events.length === 0 ? (
            <div className="py-20 text-center opacity-30">
               <CalendarIcon className="size-12 mx-auto mb-4" />
               <p className="text-sm font-bold">Free as a bird today.</p>
            </div>
          ) : (
            events.map((ev) => (
              <button
                key={ev.id}
                onClick={() => onSelectEvent(ev)}
                className="w-full group flex items-center justify-between p-4 rounded-2xl border border-border hover:border-primary/40 hover:bg-primary/[0.02] transition-all text-left"
              >
                <div className="flex items-center gap-4">
                  <div className="size-10 rounded-xl bg-primary/10 text-primary flex items-center justify-center text-xs font-bold group-hover:scale-110 transition-transform">
                    {ev.customer_name[0]}
                  </div>
                  <div>
                    <div className="text-sm font-semibold group-hover:text-primary transition-colors">{ev.customer_name}</div>
                    <div className="text-[10px] text-muted-foreground font-medium uppercase tracking-tighter">{ev.slot_start ? fmtTime(ev.slot_start) : "—"}</div>
                  </div>
                </div>
                <ChevronRight className="size-4 text-muted-foreground/30 group-hover:text-primary transition-colors" />
              </button>
            ))
          )}
        </div>
        
        <div className="mt-8 pt-6 border-t border-border/50 flex justify-center">
            <button 
              onClick={onClose}
              className="px-10 py-3 rounded-xl bg-secondary hover:bg-muted text-sm font-bold tracking-tight transition-all"
            >
              Done
            </button>
        </div>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────────────────────────────

export default function CalendarPage() {
  const [currentDate, setCurrentDate] = useState(new Date());
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(true);
  const [eventsError, setEventsError] = useState<string | null>(null);

  // View States
  const [activeTab, setActiveTab] = useState("grid");
  const [selectedDayInfo, setSelectedDayInfo] = useState<{ date: string; events: CalendarEvent[] } | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<CalendarEvent | null>(null);

  // Tab Indicator State
  const [indicatorStyle, setIndicatorStyle] = useState({ left: 4, width: 0, opacity: 0 });
  const tabsRef = useRef<(HTMLButtonElement | null)[]>([]);

  // Upcoming Panel State (Needed for AgendaView)
  const [agendaItems, setAgendaItems] = useState<CalendarEvent[]>([]);
  const [agendaLoading, setAgendaLoading] = useState(false);
  const [agendaError, setAgendaError] = useState<string | null>(null);
  const [agendaOffset, setAgendaOffset] = useState(0);
  const [agendaHasMore, setAgendaHasMore] = useState(true);
  const pageSize = 20;

  const year = currentDate.getFullYear();
  const month = currentDate.getMonth();
  const monthName = currentDate.toLocaleString("en-US", { month: "long", year: "numeric" });

  const fetchCalendarEvents = useCallback(() => {
    const startDate = `${year}-${pad(month + 1)}-01`;
    const lastDay = new Date(year, month + 1, 0).getDate();
    const endDate = `${year}-${pad(month + 1)}-${pad(lastDay)}`;

    setEventsLoading(true);
    setEventsError(null);
    api.get<CalendarEvent[]>(`/dashboard/calendar-events?start_date=${startDate}&end_date=${endDate}`)
      .then((data) => setEvents(data || []))
      .catch((e) => setEventsError(e instanceof Error ? e.message : "Failed to load events"))
      .finally(() => setEventsLoading(false));
  }, [year, month]);

  const fetchAgenda = useCallback(async (newOffset: number, refresh = false) => {
    if (agendaLoading && !refresh) return;
    setAgendaLoading(true);
    setAgendaError(null);
    try {
      const today = new Date();
      const localToday = `${today.getFullYear()}-${pad(today.getMonth() + 1)}-${pad(today.getDate())}`;
      const data = await api.get<CalendarEvent[]>(
        `/dashboard/upcoming-bookings?limit=${pageSize}&offset=${newOffset}&from_date=${localToday}`
      );
      if (newOffset === 0) {
        setAgendaItems(data || []);
      } else {
        setAgendaItems((prev) => [...prev, ...(data || [])]);
      }
      setAgendaHasMore((data?.length ?? 0) === pageSize);
      setAgendaOffset(newOffset + (data?.length ?? 0));
    } catch (e) {
      setAgendaError(e instanceof Error ? e.message : "Failed to load agenda");
    } finally {
      setAgendaLoading(false);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetchCalendarEvents();
    fetchAgenda(0);
  }, [fetchCalendarEvents, fetchAgenda]);

  // Tab indicator logic
  useEffect(() => {
    const tabs = ["grid", "agenda"];
    const activeIndex = tabs.indexOf(activeTab);
    const btn = tabsRef.current[activeIndex];
    if (btn) {
      setIndicatorStyle({
        left: btn.offsetLeft,
        width: btn.offsetWidth,
        opacity: 1
      });
    }
  }, [activeTab]);

  const handleDayClick = (day: number, dayEvents: CalendarEvent[]) => {
    const d = new Date(year, month, day);
    setSelectedDayInfo({ 
      date: d.toLocaleDateString("en-US", { weekday: "long", month: "long", day: "numeric" }), 
      events: dayEvents 
    });
  };

  const todayStr = toLocalDateStr(new Date().toISOString());

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      {/* Page header (Consistent with Business Hours) */}
      <div className="flex items-end justify-between px-1">
        <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none bg-gradient-to-br from-foreground to-foreground/70 bg-clip-text text-transparent">
            Calendar
          </h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1.5 font-medium">
            Dynamic scheduling and appointment oversight
          </p>
        </div>
      </div>

      {/* Custom Tab bar (Consistent with Business Hours) */}
      <div className="relative flex items-center gap-1 bg-secondary/50 p-1 rounded-xl w-fit max-w-full overflow-x-auto border border-border">
        <div 
          className="absolute top-1 bottom-1 bg-card rounded-lg shadow-sm border border-border/50 transition-all duration-300 ease-[cubic-bezier(0.4,0,0.2,1)]"
          style={{ 
            left: `${indicatorStyle.left}px`, 
            width: `${indicatorStyle.width}px`, 
            opacity: indicatorStyle.opacity 
          }}
        />
        <button
          ref={(el) => { tabsRef.current[0] = el; }}
          onClick={() => setActiveTab("grid")}
          className={`relative z-10 flex items-center gap-2 px-6 py-2 rounded-lg text-sm font-semibold transition-colors duration-300 ${
            activeTab === "grid" ? "text-foreground" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <LayoutGrid className="size-4" />
          Monthly Grid
        </button>
        <button
          ref={(el) => { tabsRef.current[1] = el; }}
          onClick={() => setActiveTab("agenda")}
          className={`relative z-10 flex items-center gap-2 px-6 py-2 rounded-lg text-sm font-semibold transition-colors duration-300 ${
            activeTab === "agenda" ? "text-foreground" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <Clock className="size-4" />
          Upcoming Agenda
        </button>
      </div>

      {/* Main Content Area */}
      <div key={activeTab} className="animate-in fade-in duration-500 fill-mode-both">
        {activeTab === "grid" ? (
          <div className="space-y-6">
            {/* Grid-specific navigation & controls (now inside the tab view) */}
            <div className="flex flex-wrap items-center justify-between gap-3 sm:gap-4 py-2 border-b border-border/50">
              <div className="flex items-center gap-1 sm:gap-2 p-1 bg-muted/40 rounded-xl border border-border/50">
                <button
                  onClick={() => setCurrentDate(new Date())}
                  className="h-8 px-4 rounded-lg hover:bg-card hover:shadow-sm text-[10px] font-bold tracking-widest uppercase transition-all"
                >
                  Today
                </button>
                <div className="w-px h-3 bg-border/50" />
                <div className="flex items-center gap-0.5">
                  <button
                    onClick={() => setCurrentDate(new Date(year, month - 1, 1))}
                    title="Previous month"
                    className="p-1.5 rounded-lg hover:bg-card text-muted-foreground hover:text-foreground transition-all"
                  >
                    <ChevronLeft className="size-4" />
                  </button>
                  <div className="px-2 text-sm font-semibold min-w-[120px] text-center">{monthName}</div>
                  <button
                    onClick={() => setCurrentDate(new Date(year, month + 1, 1))}
                    title="Next month"
                    className="p-1.5 rounded-lg hover:bg-card text-muted-foreground hover:text-foreground transition-all"
                  >
                    <ChevronRight className="size-4" />
                  </button>
                </div>
              </div>

              {/* Legend (moved inside grid tab) */}
              <div className="hidden sm:flex items-center gap-6 px-4">
                <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                  <div className="size-2 rounded-full bg-primary" /> Today
                </div>
                <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                  <div className="size-2 rounded-full bg-rose-400" /> Weekend
                </div>
              </div>
            </div>

            <div className="relative">
              {/* Flickering blue line removed per user request */}
              
              <div className={`bg-card border dark:border-white/10 border-black/10 rounded-2xl overflow-hidden shadow-sm transition-all duration-500 ${eventsLoading && events.length === 0 ? "opacity-60 scale-[0.99]" : "opacity-100 scale-100"}`}>
                {/* Day headers */}
                <div className="grid grid-cols-7 border-b dark:border-white/10 border-black/10 bg-muted/5">
                  {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((d, i) => (
                    <div key={d} className={`px-1 sm:px-2 py-3 sm:py-4 text-center text-[9px] sm:text-[10px] font-semibold uppercase tracking-[0.15em] sm:tracking-[0.2em] ${i === 0 || i === 6 ? "text-rose-400" : "text-muted-foreground"}`}>
                      <span className="sm:hidden">{d.charAt(0)}</span>
                      <span className="hidden sm:inline">{d}</span>
                    </div>
                  ))}
                </div>

                {/* Grid Cells */}
                <div className="grid grid-cols-7 auto-rows-fr">
                  {getMonthDays(year, month).map((day, idx) => {
                    if (day === null) return <div key={`e-${idx}`} className="aspect-square sm:aspect-auto sm:min-h-[120px] bg-muted/[0.02] border-r border-b border-border/10" />;
                    
                    const cellStr = `${year}-${pad(month + 1)}-${pad(day)}`;
                    const isToday = cellStr === todayStr;
                    const isPast = cellStr < todayStr;
                    const isWeekend = idx % 7 === 0 || idx % 7 === 6;
                    const dayEvents = events.filter(e => e.slot_start && toLocalDateStr(e.slot_start) === cellStr);

                    return (
                      <div
                        key={cellStr}
                        onClick={() => handleDayClick(day, dayEvents)}
                        className={[
                          "group relative aspect-square sm:aspect-auto sm:min-h-[110px] p-1.5 sm:p-3 cursor-pointer border-r border-b dark:border-white/10 border-black/10 transition-all duration-300",
                          "hover:bg-primary/[0.03] hover:z-10",
                          isToday ? "bg-primary/[0.04] z-10" : "bg-card",
                          isWeekend ? "bg-rose-500/[0.08]" : "",
                          isPast ? "opacity-[0.65] bg-muted/20 bg-[repeating-linear-gradient(45deg,transparent,transparent_10px,rgba(128,128,128,0.05)_10px,rgba(128,128,128,0.05)_20px)]" : ""
                        ].filter(Boolean).join(" ")}
                      >
                        <div className={[
                          "size-6 sm:size-7 flex items-center justify-center rounded-lg text-xs sm:text-sm font-semibold transition-all group-hover:scale-110",
                          isToday ? "bg-primary text-primary-foreground shadow-sm shadow-primary/30" : (isPast ? "text-muted-foreground/60" : "text-foreground group-hover:text-primary")
                        ].join(" ")}>
                          {day}
                        </div>

                        <div className="mt-2 sm:mt-3 space-y-1">
                          {dayEvents.slice(0, 3).map((ev) => (
                            <div 
                              key={ev.id} 
                              className={`text-[8px] sm:text-[9px] font-semibold px-1.5 sm:px-2 py-1 rounded-md truncate border transition-all ${
                                isPast 
                                  ? "bg-muted/40 text-muted-foreground/60 border-muted-foreground/20 border-dashed" 
                                  : "bg-primary/10 text-primary border-primary/10 group-hover:border-primary/30"
                              }`}
                            >
                               {ev.slot_start ? fmtTime(ev.slot_start) : ""} {ev.customer_name}
                            </div>
                          ))}
                          {dayEvents.length > 3 && (
                            <div className="text-[8px] sm:text-[9px] font-bold text-muted-foreground/50 px-1.5 sm:px-2 tracking-tighter">
                              +{dayEvents.length - 3} OTHERS
                            </div>
                          )}
                        </div>

                        {/* Visual Indicators */}
                        <div className="absolute top-2 right-2 sm:top-3 sm:right-3 flex flex-col gap-1">
                          {isToday && (
                             <div className="size-2 rounded-full bg-primary animate-pulse" />
                          )}
                          {isPast && !isToday && (
                             <div className="size-1 rounded-full bg-muted-foreground/30" />
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {eventsLoading && events.length === 0 && (
                 <div className="flex flex-col items-center justify-center min-h-[500px] border border-border rounded-2xl bg-card/50 backdrop-blur-sm space-y-6">
                   <LoaderPulseSquare className="size-20" />
                   <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground animate-pulse">Syncing Calendar Data...</p>
                 </div>
              )}
            </div>
          </div>
        ) : (
          <AgendaView 
            items={agendaItems}
            loading={agendaLoading}
            error={agendaError}
            onRefresh={() => fetchAgenda(0, true)}
            onSelectEvent={setSelectedEvent}
            hasMore={agendaHasMore}
            onLoadMore={() => fetchAgenda(agendaOffset)}
          />
        )}
      </div>

      {/* Modern Centered Dialogs */}
      {selectedDayInfo && (
        <DayEventsDialog 
          dateStr={selectedDayInfo.date}
          events={selectedDayInfo.events}
          onClose={() => setSelectedDayInfo(null)}
          onSelectEvent={(ev) => {
            setSelectedDayInfo(null);
            setSelectedEvent(ev);
          }}
        />
      )}

      <BookingDialog 
        event={selectedEvent}
        onClose={() => setSelectedEvent(null)}
      />
    </div>
  );
}
