"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Search, X } from "lucide-react";
import { api } from "@/lib/api";

export type OrgRow = {
  id: string;
  name: string;
  business_name: string | null;
};

type OrgComboboxProps = {
  value: string | string[];
  onSelect: (org: OrgRow) => void;
  onRemove?: (id: string) => void;
  placeholder?: string;
  multiple?: boolean;
  pageSize?: number;
  className?: string;
};

export function OrgCombobox({
  value,
  onSelect,
  onRemove,
  placeholder = "Type to search organizations…",
  multiple = false,
  pageSize = 20,
  className = "",
}: OrgComboboxProps) {
  const ids = multiple ? (value as string[]) : value ? [value as string] : [];
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<OrgRow[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState(-1);
  const [known, setKnown] = useState<Record<string, OrgRow>>({});
  const rootRef = useRef<HTMLDivElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const seq = useRef(0);

  const idKey = ids.slice().sort().join("|");

  useEffect(() => {
    let cancelled = false;
    const missing = ids.filter((id) => !known[id]);
    if (missing.length === 0) return;
    for (const id of missing) {
      api
        .get<{ id: string; name: string; business_name?: string | null }>(
          `/tenants/orgs/${id}`
        )
        .then((o) => {
          if (cancelled) return;
          setKnown((k) => ({
            ...k,
            [id]: {
              id: o.id,
              name: o.name,
              business_name: o.business_name ?? null,
            },
          }));
        })
        .catch(() => {
          // keep unlabelled — id fallback is shown instead
        });
    }
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idKey]);

  const runSearch = useCallback(
    async (term: string) => {
      const current = ++seq.current;
      setLoading(true);
      try {
        const res = await api.get<{ items: OrgRow[] }>(
          `/tenants/orgs?search=${encodeURIComponent(term)}&page_size=${pageSize}`
        );
        if (current !== seq.current) return;
        const mapped = res.items.map((o) => ({
          id: o.id,
          name: o.name,
          business_name: o.business_name ?? null,
        }));
        setOptions(mapped);
        setKnown((k) => {
          const next = { ...k };
          for (const o of mapped) {
            if (!next[o.id]) next[o.id] = o;
          }
          return next;
        });
        setActive(-1);
        setOpen(true);
      } catch {
        // transient search failure — keep previous options
      } finally {
        if (current === seq.current) setLoading(false);
      }
    },
    [pageSize]
  );

  const onQueryChange = (t: string) => {
    setQuery(t);
    if (timer.current) clearTimeout(timer.current);
    const term = t.trim();
    if (!term) {
      setOptions([]);
      setOpen(false);
      setLoading(false);
      return;
    }
    timer.current = setTimeout(() => void runSearch(term), 250);
  };

  const selectOrg = (org: OrgRow) => {
    onSelect(org);
    if (!multiple) {
      setQuery("");
      setOptions([]);
      setOpen(false);
    }
  };

  const isSelected = (id: string) => ids.includes(id);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  const selectedOrgs = ids
    .map((id) => known[id])
    .filter((o): o is OrgRow => Boolean(o));

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      {selectedOrgs.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          {selectedOrgs.map((o) => (
            <span
              key={o.id}
              className="inline-flex items-center gap-1 rounded-md border border-indigo-900 bg-indigo-950/50 px-2 py-1 text-xs text-indigo-200"
            >
              {o.name}
              <button
                type="button"
                aria-label={`Remove ${o.name}`}
                onClick={() => onRemove?.(o.id)}
                className="rounded p-0.5 text-indigo-400 hover:text-indigo-100"
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-500" />
        <input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setActive((a) => (a + 1) % Math.max(options.length, 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) =>
                a <= 0 ? Math.max(options.length - 1, 0) : a - 1
              );
            } else if (e.key === "Enter") {
              e.preventDefault();
              if (open && options[active]) selectOrg(options[active]);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
          onFocus={() => {
            if (options.length > 0) setOpen(true);
          }}
          placeholder={placeholder}
          className="w-full rounded-md border border-slate-700 bg-slate-950 py-2 pl-9 pr-8 text-sm text-slate-100 outline-none placeholder:text-slate-500 focus:border-indigo-500"
        />
        {query && (
          <button
            type="button"
            aria-label="Clear search"
            onClick={() => onQueryChange("")}
            className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-0.5 text-slate-500 hover:text-slate-200"
          >
            <X className="size-4" />
          </button>
        )}
      </div>
      {open && (
        <div className="absolute z-10 mt-1 max-h-64 w-full overflow-y-auto rounded-md border border-slate-700 bg-slate-950 shadow-lg">
          {loading && options.length === 0 && (
            <div className="px-3 py-2 text-sm text-slate-500">Searching…</div>
          )}
          {!loading && options.length === 0 && (
            <div className="px-3 py-2 text-sm text-slate-500">No organizations found</div>
          )}
          {options.map((o, i) => {
            const selected = isSelected(o.id);
            return (
              <button
                key={o.id}
                type="button"
                onClick={() => selectOrg(o)}
                onMouseEnter={() => setActive(i)}
                className={`block w-full px-3 py-2 text-left ${
                  i === active ? "bg-slate-900" : ""
                } ${selected ? "bg-indigo-950/40" : ""}`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    {multiple && (
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => selectOrg(o)}
                        onClick={(e) => e.stopPropagation()}
                        className="size-3.5 shrink-0 accent-indigo-500"
                      />
                    )}
                    <span>
                      <span className="text-slate-200">{o.name}</span>
                      {o.business_name && (
                        <span className="ml-2 text-xs text-slate-500">
                          {o.business_name}
                        </span>
                      )}
                    </span>
                  </span>
                  {!multiple && selected && (
                    <Check className="size-4 shrink-0 text-indigo-400" />
                  )}
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}