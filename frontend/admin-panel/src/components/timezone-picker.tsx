"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";

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

export const TIMEZONES: string[] = (() => {
  try {
    const supported = Intl.supportedValuesOf("timeZone");
    if (Array.isArray(supported) && supported.length > 0) return [...supported].sort();
  } catch {
    // Intl.supportedValuesOf unavailable — use the curated list below.
  }
  return FALLBACK_TIMEZONES;
})();

// ── Searchable timezone picker (all IANA zones) ──────────────────────────

export function TimezonePicker({ value, onChange }: { value: string; onChange: (tz: string) => void }) {
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
