"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Pagination } from "@/components/pagination";
import {
  SearchInput,
  CheckboxFilter,
  DateFilter,
  SortableTh,
  isDateRangeValid,
  toApiDate,
  toApiDateTo,
} from "@/components/filter-bar";

type UserRow = {
  id: string;
  org_id: string;
  org_name: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string | null;
};

type UserList = {
  total: number;
  page: number;
  page_size: number;
  items: UserRow[];
};

const PAGE_SIZE = 10;

const STATUS_OPTIONS = [
  { value: "true", label: "Active" },
  { value: "false", label: "Disabled" },
];

const SORT_DEFAULTS: Record<string, "asc" | "desc"> = {
  created_at: "desc",
  email: "asc",
  full_name: "asc",
};

export default function UsersPage() {
  const { can } = useAuth();
  const [data, setData] = useState<UserList | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilters, setStatusFilters] = useState<string[]>([]);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [unmask, setUnmask] = useState(false);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const canUnmask = can("pii.unmask");

  const handleSort = (column: string, order: "asc" | "desc") => {
    setSortBy(column);
    setSortOrder(order);
    setPage(1);
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    if (!isDateRangeValid(fromDate, toDate)) {
      setData({ items: [], total: 0, page, page_size: PAGE_SIZE });
      setLoading(false);
      return;
    }
    try {
      const params = new URLSearchParams();
      params.set("page", String(page));
      params.set("page_size", String(PAGE_SIZE));
      if (search) params.set("search", search);
      for (const s of statusFilters) params.append("is_active", s);
      if (fromDate) params.set("from_date", toApiDate(fromDate) || fromDate);
      if (toDate) params.set("to_date", toApiDateTo(toDate) || toDate);
      params.set("sort_by", sortBy);
      params.set("sort_order", sortOrder);
      if (unmask && canUnmask) params.set("unmask", "true");
      setData(await api.get<UserList>(`/tenants/users?${params.toString()}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  }, [search, statusFilters, fromDate, toDate, sortBy, sortOrder, unmask, canUnmask, page]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <h1 className="text-2xl font-bold">Users</h1>
      <p className="mt-1 text-sm text-slate-400">
        Cross-organization user search. Emails and names are masked by default.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          placeholder="Search email or full name…"
          className="w-64"
        />
        <CheckboxFilter
          label="Status"
          options={STATUS_OPTIONS}
          selected={statusFilters}
          onChange={(v) => {
            setStatusFilters(v);
            setPage(1);
          }}
        />
        <DateFilter
          label="Joined"
          fromDate={fromDate}
          toDate={toDate}
          onFromChange={setFromDate}
          onToChange={setToDate}
        />
        {canUnmask && (
          <button
            onClick={() => setUnmask((v) => !v)}
            className={`ml-auto rounded-md border px-3 py-2 text-sm ${
              unmask
                ? "border-amber-700 bg-amber-950/40 text-amber-300"
                : "border-slate-700 text-slate-300 hover:bg-slate-800"
            }`}
          >
            {unmask ? "Showing unmasked PII" : "Unmask PII"}
          </button>
        )}
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-6 overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <SortableTh
                column="full_name"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.full_name}
              >
                Name
              </SortableTh>
              <SortableTh
                column="email"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.email}
              >
                Email
              </SortableTh>
              <th className="px-4 py-3">Organization</th>
              <th className="px-4 py-3">Status</th>
              <SortableTh
                column="created_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.created_at}
              >
                Joined
              </SortableTh>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {loading && (
              <tr>
                <td colSpan={5} className="px-4 py-3 text-slate-500">
                  Loading…
                </td>
              </tr>
            )}
            {!loading &&
              data?.items.map((u) => (
                <tr key={u.id}>
                  <td className="px-4 py-3 font-medium">
                    <Link
                      href={`/dashboard/users/${u.id}`}
                      className="text-indigo-400 hover:underline"
                    >
                      {u.full_name ?? "—"}
                    </Link>
                  </td>
                  <td className="px-4 py-3 text-slate-300">{u.email}</td>
                  <td className="px-4 py-3">
                    <Link
                      href={`/dashboard/organizations/${u.org_id}`}
                      className="text-indigo-400 hover:underline"
                    >
                      {u.org_name}
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        u.is_active
                          ? "bg-green-900/60 text-green-300"
                          : "bg-red-900/60 text-red-300"
                      }`}
                    >
                      {u.is_active ? "ACTIVE" : "DISABLED"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}
                  </td>
                </tr>
              ))}
            {!loading && data?.items.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  No users found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {data && (
        <Pagination
          page={page}
          pageSize={PAGE_SIZE}
          total={data.total}
          onPageChange={setPage}
        />
      )}
    </div>
  );
}
