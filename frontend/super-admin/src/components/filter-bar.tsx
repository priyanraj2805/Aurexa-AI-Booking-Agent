"use client";

import { useEffect, useRef, useState } from "react";
import { Search, X, ChevronDown, ChevronUp, Calendar, ArrowUpDown } from "lucide-react";

type SearchInputProps = {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  delay?: number;
};

export function SearchInput({
  value,
  onChange,
  placeholder = "Search…",
  className = "",
  delay = 300,
}: SearchInputProps) {
  const [draft, setDraft] = useState(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (draft !== value) onChange(draft);
    }, delay);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [draft, delay, value]);

  return (
    <div className={`relative ${className}`}>
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-500" />
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-md border border-slate-700 bg-slate-900 py-2 pl-9 pr-8 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-indigo-500"
      />
      {draft && (
        <button
          onClick={() => setDraft("")}
          aria-label="Clear search"
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-500 hover:text-slate-200"
        >
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}

type CheckboxOption = { value: string; label: string };

type CheckboxFilterProps = {
  label: string;
  options: CheckboxOption[];
  selected: string[];
  onChange: (values: string[]) => void;
  className?: string;
};

export function CheckboxFilter({ label, options, selected, onChange, className }: CheckboxFilterProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  const toggle = (value: string) => {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  };

  const selectedCount = selected.length;
  const displayLabel = selectedCount > 0 ? `${label} (${selectedCount})` : label;

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-300 hover:bg-slate-900 focus:outline-none focus:border-indigo-500"
      >
        <span>{displayLabel}</span>
        {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
      </button>
      {open && (
        <div className="absolute z-10 mt-1 w-56 rounded-md border border-slate-700 bg-slate-950 shadow-lg">
          {options.map((opt) => {
            const checked = selected.includes(opt.value);
            return (
              <label
                key={opt.value}
                className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                  checked ? "bg-indigo-950/60 text-indigo-200" : "text-slate-300 hover:bg-slate-900"
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(opt.value)}
                  className="size-3.5 accent-indigo-500"
                />
                {opt.label}
              </label>
            );
          })}
          {selected.length > 0 && (
            <div className="border-t border-slate-700 px-3 py-2">
              <button
                onClick={() => onChange([])}
                className="w-full text-left text-xs text-slate-400 hover:text-slate-200"
              >
                Clear all
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

type DateFilterProps = {
  label: string;
  fromDate: string;
  toDate: string;
  onFromChange: (value: string) => void;
  onToChange: (value: string) => void;
  className?: string;
};

const FULL_DATE_DMY = /^(\d{2})-(\d{2})-(\d{4})$/;

export const isValidDateValue = (value: string) => {
  const m = FULL_DATE_DMY.exec(value);
  if (!m) return false;
  const d = new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
  return d.getFullYear() === Number(m[3]) && d.getMonth() === Number(m[2]) - 1 && d.getDate() === Number(m[1]);
};

const toIsoDate = (value: string) => {
  const m = FULL_DATE_DMY.exec(value);
  if (!m) return null;
  return `${m[3]}-${m[2]}-${m[1]}`;
};

export const toApiDate = (value: string) => (isValidDateValue(value) ? toIsoDate(value) : null);

export const toApiDateTo = (value: string) => (isValidDateValue(value) ? toIsoDate(value) : null);

export const isDateRangeValid = (fromDate: string, toDate: string) => {
  if (fromDate && !isValidDateValue(fromDate)) return false;
  if (toDate && !isValidDateValue(toDate)) return false;
  const from = toApiDate(fromDate) || "";
  const to = toApiDateTo(toDate) || "";
  return !from || !to || from <= to;
};

const INVALID_DATE_MARKER = "__INVALID_DATE__";

function maskDate(raw: string): string {
  const digits = raw.replace(/\D/g, "").slice(0, 8);
  if (digits.length <= 2) return digits;
  if (digits.length <= 4) return `${digits.slice(0, 2)}-${digits.slice(2)}`;
  return `${digits.slice(0, 2)}-${digits.slice(2, 4)}-${digits.slice(4)}`;
}

function DateField({
  value,
  onChange,
  invalid,
}: {
  value: string;
  onChange: (value: string) => void;
  invalid: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const pickerRef = useRef<HTMLInputElement>(null);
  const [text, setText] = useState(value === INVALID_DATE_MARKER ? "" : value);

  useEffect(() => {
    const next = value === INVALID_DATE_MARKER ? "" : value;
    setText(next);
    if (inputRef.current && inputRef.current.value !== next) inputRef.current.value = next;
  }, [value]);

  const commit = (t: string) => {
    if (!t) {
      onChange("");
      return;
    }
    onChange(isValidDateValue(t) ? t : INVALID_DATE_MARKER);
  };

  const openPicker = () => {
    const picker = pickerRef.current;
    if (!picker) return;
    try {
      if (typeof picker.showPicker === "function") picker.showPicker();
      else picker.click();
    } catch {
      picker.focus();
    }
  };

  return (
    <div className="relative">
      <input
        ref={inputRef}
        type="text"
        inputMode="numeric"
        autoComplete="off"
        placeholder="DD-MM-YYYY"
        maxLength={10}
        defaultValue={value === INVALID_DATE_MARKER ? "" : value}
        onFocus={() => {
          if (value === INVALID_DATE_MARKER) onChange("");
        }}
        onInput={(e) => {
          const el = e.currentTarget;
          const masked = maskDate(el.value);
          if (masked !== el.value) el.value = masked;
          setText(masked);
        }}
        onBlur={() => commit(inputRef.current?.value ?? text)}
        className={`w-36 rounded-md border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-indigo-500 ${
          invalid ? "border-red-500 focus:border-red-500" : ""
        }`}
      />
      <button
        type="button"
        onClick={openPicker}
        aria-label="Pick date from calendar"
        className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-500 hover:text-slate-200"
      >
        <Calendar className="size-4" />
      </button>
      <input
        ref={pickerRef}
        type="date"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(e) => {
          const v = e.target.value;
          e.target.value = "";
          if (!v) return;
          const [y, m, d] = v.split("-");
          const display = `${d}-${m}-${y}`;
          setText(display);
          commit(display);
        }}
        className="pointer-events-none absolute h-px w-px opacity-0"
      />
    </div>
  );
}

export function DateFilter({ label, fromDate, toDate, onFromChange, onToChange, className }: DateFilterProps) {
  const hasFilter = fromDate || toDate;
  const displayLabel = hasFilter ? `${label} ✓` : label;
  const invalidFrom = Boolean(fromDate && !isValidDateValue(fromDate));
  const invalidTo = Boolean(toDate && !isValidDateValue(toDate));
  const inverted = Boolean(
    fromDate && toDate && !invalidFrom && !invalidTo && (toApiDate(fromDate) || "") > (toApiDateTo(toDate) || "")
  );
  const invalid = invalidFrom || invalidTo || inverted;
  const hint = invalidFrom || invalidTo
    ? "Enter a valid date (DD-MM-YYYY) — showing no results"
    : "From must be before To — showing no results";

  const clear = () => {
    onFromChange("");
    onToChange("");
  };

  return (
    <div className={`relative ${className}`}>
      <label className="flex items-center gap-2">
        <span className="text-sm text-slate-400">{displayLabel}</span>
        <div className="flex items-center gap-2">
          <DateField value={fromDate} onChange={onFromChange} invalid={invalidFrom || inverted} />
          <span className="text-slate-500">–</span>
          <DateField value={toDate} onChange={onToChange} invalid={invalidTo || inverted} />
          {hasFilter && (
            <button
              onClick={clear}
              className="text-slate-400 hover:text-slate-200"
            >
              <X className="size-4" />
            </button>
          )}
        </div>
      </label>
      {invalid && <p className="mt-1 text-xs text-red-400">{hint}</p>}
    </div>
  );
}

type SortableThProps = {
  column: string;
  sortBy: string;
  sortOrder: "asc" | "desc";
  onSort: (column: string, order: "asc" | "desc") => void;
  defaultOrder?: "asc" | "desc";
  className?: string;
  children: React.ReactNode;
};

export function SortableTh({
  column,
  sortBy,
  sortOrder,
  onSort,
  defaultOrder = "desc",
  className = "",
  children,
}: SortableThProps) {
  const active = sortBy === column;
  const nextOrder: "asc" | "desc" = active
    ? sortOrder === "desc"
      ? "asc"
      : "desc"
    : defaultOrder;
  return (
    <th className={`px-4 py-3 ${className}`}>
      <button
        type="button"
        onClick={() => onSort(column, nextOrder)}
        className={`flex items-center gap-1 transition-colors ${
          active
            ? "text-indigo-300"
            : "text-slate-400 hover:text-slate-200"
        }`}
        title={`Sort by ${String(children).toLowerCase()}`}
      >
        <span>{children}</span>
        {active &&
          (sortOrder === "asc" ? (
            <ChevronUp className="size-3.5" />
          ) : (
            <ChevronDown className="size-3.5" />
          ))}
      </button>
    </th>
  );
}

type SortSelectProps = {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  className?: string;
};

export function SortSelect({ value, onChange, options, className }: SortSelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-300 outline-none focus:border-indigo-500 ${className}`}
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

type SortOrderProps = {
  value: "asc" | "desc";
  onChange: (value: "asc" | "desc") => void;
  className?: string;
};

export function SortOrder({ value, onChange, className }: SortOrderProps) {
  return (
    <button
      onClick={() => onChange(value === "asc" ? "desc" : "asc")}
      className={`rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-300 outline-none focus:border-indigo-500 ${className}`}
      title="Toggle sort order"
    >
      {value === "asc" ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
    </button>
  );
}