"use client";

import { useEffect, useRef, useState } from "react";
import { Search, X, ChevronDown, ChevronUp, Calendar, ArrowUpDown } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select";

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
      <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={placeholder}
        className="w-full min-w-0 rounded-md border border-input bg-background py-2 pl-9 pr-8 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-primary"
      />
      {draft && (
        <button
          onClick={() => setDraft("")}
          aria-label="Clear search"
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      )}
    </div>
  );
}

type CheckboxOption = { value: string; label: string };

type FilterSelectProps = {
  value: string;
  onChange: (value: string) => void;
  options: CheckboxOption[];
  className?: string;
};

export function FilterSelect({ value, onChange, options, className }: FilterSelectProps) {
  const selectedOption = options.find((option) => option.value === value) ?? options[0];

  return (
    <Select
      value={value || "__all__"}
      onValueChange={(next) => onChange(!next || next === "__all__" ? "" : next)}
    >
      <SelectTrigger className={`h-9 w-full min-w-0 rounded-md px-3 text-xs xl:w-auto ${className ?? ""}`}>
        <span className="min-w-0 flex-1 truncate text-left">
          {selectedOption?.label ?? "Select an option"}
        </span>
      </SelectTrigger>
      <SelectContent
        align="start"
        className="max-h-[min(23rem,var(--available-height))] max-w-[calc(100vw-2rem)]"
      >
        {options.map((option) => (
          <SelectItem
            key={option.value || "__all__"}
            value={option.value || "__all__"}
            className="h-8"
          >
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

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
    <div ref={rootRef} className={`relative min-w-0 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full min-w-0 items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground hover:bg-accent focus:outline-none focus:border-primary xl:w-full"
      >
        <span>{displayLabel}</span>
        {open ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
      </button>
      {open && (
        <div className="absolute z-10 mt-1 w-56 rounded-md border border-border bg-popover text-popover-foreground shadow-lg">
          {options.map((opt) => {
            const checked = selected.includes(opt.value);
            return (
              <label
                key={opt.value}
                className={`flex cursor-pointer items-center gap-2 px-3 py-2 text-sm ${
                  checked ? "bg-primary/10 text-primary" : "text-foreground hover:bg-accent"
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggle(opt.value)}
                  className="size-3.5 accent-primary"
                />
                {opt.label}
              </label>
            );
          })}
          {selected.length > 0 && (
            <div className="border-t border-border px-3 py-2">
              <button
                onClick={() => onChange([])}
                className="w-full text-left text-xs text-muted-foreground hover:text-foreground"
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
    <div className="relative min-w-0 flex-1 sm:flex-none">
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
        className={`w-full rounded-md border border-border bg-background px-2 py-2 pr-8 text-xs outline-none placeholder:text-muted-foreground focus:border-primary sm:w-36 sm:py-1.5 sm:text-sm xl:w-32 ${
          invalid ? "border-destructive focus:border-destructive" : ""
        }`}
      />
      <button
        type="button"
        onClick={openPicker}
        aria-label="Pick date from calendar"
        className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
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
    <div className={`relative w-full min-w-0 sm:w-auto xl:flex xl:items-center xl:gap-2 ${className}`}>
      <div className="mb-2 flex items-center justify-between gap-2 sm:mb-0 xl:shrink-0">
        <span className="text-sm text-muted-foreground">{displayLabel}</span>
        {hasFilter && (
          <button
            type="button"
            onClick={clear}
            aria-label="Clear date range"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <X className="size-3.5" />
            <span className="sm:hidden">Clear</span>
          </button>
        )}
      </div>
      <div className="grid min-w-0 grid-cols-1 gap-2 sm:flex sm:w-auto sm:items-center sm:gap-2 xl:gap-1.5">
        <div className="grid min-w-0 grid-cols-[40px_minmax(0,1fr)] items-center gap-2 sm:block">
          <span className="text-xs text-muted-foreground sm:hidden">From</span>
          <DateField value={fromDate} onChange={onFromChange} invalid={invalidFrom || inverted} />
        </div>
        <span className="hidden text-muted-foreground sm:inline">–</span>
        <div className="grid min-w-0 grid-cols-[40px_minmax(0,1fr)] items-center gap-2 sm:block">
          <span className="text-xs text-muted-foreground sm:hidden">To</span>
          <DateField value={toDate} onChange={onToChange} invalid={invalidTo || inverted} />
        </div>
      </div>
      {invalid && <p className="mt-1 text-xs text-destructive">{hint}</p>}
    </div>
  );
}

type SortableThProps = {
  column: string;
  sortBy: string;
  sortOrder: "asc" | "desc";
  /** `null` = third click on an already-flipped column: reset to the table's default sort. */
  onSort: (column: string, order: "asc" | "desc" | null) => void;
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
  const flipOrder: "asc" | "desc" = sortOrder === "desc" ? "asc" : "desc";
  const label = String(children).toLowerCase().trim();
  const handleClick = () => {
    if (!active) {
      // First click: sort this column in its natural/default direction.
      onSort(column, defaultOrder);
    } else if (sortOrder === defaultOrder) {
      // Second click: flip the direction.
      onSort(column, flipOrder);
    } else {
      // Third click: undo the sort entirely — back to table default.
      onSort(column, null);
    }
  };
  return (
    <th className={`px-4 py-3 ${className}`}>
      <button
        type="button"
        onClick={handleClick}
        className={`flex items-center gap-1 transition-colors ${
          active
            ? "text-primary"
            : "text-muted-foreground hover:text-foreground"
        }`}
        title={
          active && sortOrder !== defaultOrder
            ? `Sorted by ${label} — click to reset`
            : `Sort by ${label}`
        }
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
      className={`rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary ${className}`}
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
      className={`rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary ${className}`}
      title="Toggle sort order"
    >
      {value === "asc" ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}
    </button>
  );
}