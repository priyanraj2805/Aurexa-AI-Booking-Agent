"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Clock } from "lucide-react";

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

// ── Time picker (AM/PM + hour + 15-min grid, portal popup) ──────────────
// Same component used by Business Hours and per-doctor Hours.

export function TimeSelect({
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
