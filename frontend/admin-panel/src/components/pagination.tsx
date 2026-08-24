"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

type PaginationProps = {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
};

function pageNumbers(current: number, totalPages: number): (number | "…")[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const pages: (number | "…")[] = [1];
  const start = Math.max(2, current - 1);
  const end = Math.min(totalPages - 1, current + 1);
  if (start > 2) pages.push("…");
  for (let p = start; p <= end; p++) pages.push(p);
  if (end < totalPages - 1) pages.push("…");
  pages.push(totalPages);
  return pages;
}

export function Pagination({ page, pageSize, total, onPageChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= 0) return null;
  const from = (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const btn =
    "inline-flex h-8 items-center gap-1 rounded-md border border-border px-2.5 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40";
  const activeBtn =
    "inline-flex h-8 items-center rounded-md bg-primary px-2.5 text-sm font-medium text-primary-foreground";

  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <span className="text-sm text-muted-foreground">
        Showing {from}–{to} of {total}
      </span>
      <div className="flex items-center gap-1.5">
        <button
          className={btn}
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          <ChevronLeft className="size-3.5" />
          Previous
        </button>
        {pageNumbers(page, totalPages).map((p, i) =>
          p === "…" ? (
            <span key={`e${i}`} className="px-1 text-sm text-muted-foreground">
              …
            </span>
          ) : (
            <button
              key={p}
              className={p === page ? activeBtn : btn}
              onClick={() => onPageChange(p)}
            >
              {p}
            </button>
          )
        )}
        <button
          className={btn}
          disabled={page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
          <ChevronRight className="size-3.5" />
        </button>
      </div>
    </div>
  );
}