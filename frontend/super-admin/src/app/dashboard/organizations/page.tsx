"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal } from "@/components/modal";
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

type OrgRow = {
  id: string;
  name: string;
  business_name: string | null;
  is_active: boolean;
  created_at: string | null;
  user_count: number;
  booking_count: number;
  conversation_count: number;
  session_retention_days: number | null;
  last_activity_at: string | null;
};

type OrgList = {
  total: number;
  page: number;
  page_size: number;
  items: OrgRow[];
};

const PAGE_SIZE = 10;

const STATUS_OPTIONS = [
  { value: "true", label: "Active" },
  { value: "false", label: "Suspended" },
];

const SORT_DEFAULTS: Record<string, "asc" | "desc"> = {
  created_at: "desc",
  name: "asc",
};

export default function OrganizationsPage() {
  const { can } = useAuth();
  const { toast } = useToast();
  const [data, setData] = useState<OrgList | null>(null);
  const [search, setSearch] = useState("");
  const [statusFilters, setStatusFilters] = useState<string[]>([]);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ name: "", business_name: "", is_active: true });
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<{ org_id: string; api_key: string } | null>(null);

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
      setData(await api.get<OrgList>(`/tenants/orgs?${params.toString()}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load organizations");
    } finally {
      setLoading(false);
    }
  }, [search, statusFilters, fromDate, toDate, sortBy, sortOrder, page]);

  useEffect(() => {
    load();
  }, [load]);

  const createOrg = async () => {
    setCreating(true);
    try {
      const res = await api.post<{ org_id: string; api_key: string }>(
        "/lifecycle/orgs",
        {
          name: form.name,
          business_name: form.business_name || null,
          is_active: form.is_active,
        }
      );
      setCreated(res);
      toast("success", `${form.name} created`);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to create organization");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Organizations</h1>
          <p className="mt-1 text-sm text-slate-400">
            All tenant organizations — read via the SELECT-only database role.
          </p>
        </div>
        {can("orgs.create") && (
          <button
            onClick={() => {
              setForm({ name: "", business_name: "", is_active: true });
              setCreated(null);
              setCreateOpen(true);
            }}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            New organization
          </button>
        )}
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          placeholder="Search name or business name…"
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
          label="Created"
          fromDate={fromDate}
          toDate={toDate}
          onFromChange={setFromDate}
          onToChange={setToDate}
        />
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
                column="name"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.name}
              >
                Organization
              </SortableTh>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Users</th>
              <th className="px-4 py-3">Bookings</th>
              <th className="px-4 py-3">Conversations</th>
              <SortableTh
                column="created_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.created_at}
              >
                Created
              </SortableTh>
              <th className="px-4 py-3">Last activity</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {loading &&
              [0, 1, 2].map((i) => (
                <tr key={i}>
                  <td colSpan={7} className="px-4 py-3 text-slate-500">
                    Loading…
                  </td>
                </tr>
              ))}
            {!loading &&
              data?.items.map((o) => (
                <tr key={o.id}>
                  <td className="px-4 py-3">
                    <Link
                      href={`/dashboard/organizations/${o.id}`}
                      className="font-medium text-indigo-400 hover:underline"
                    >
                      {o.name}
                    </Link>
                    {o.business_name && (
                      <div className="text-xs text-slate-500">
                        {o.business_name}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        o.is_active
                          ? "bg-green-900/60 text-green-300"
                          : "bg-red-900/60 text-red-300"
                      }`}
                    >
                      {o.is_active ? "ACTIVE" : "SUSPENDED"}
                    </span>
                  </td>
                  <td className="px-4 py-3">{o.user_count}</td>
                  <td className="px-4 py-3">{o.booking_count}</td>
                  <td className="px-4 py-3">{o.conversation_count}</td>
                  <td className="px-4 py-3 text-slate-400">
                    {o.created_at
                      ? new Date(o.created_at).toLocaleString()
                      : "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {o.last_activity_at
                      ? new Date(o.last_activity_at).toLocaleString()
                      : "—"}
                  </td>
                </tr>
              ))}
            {!loading && data?.items.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-8 text-center text-slate-400">
                  No organizations found
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

      {createOpen && (
        <Modal
          title="New organization"
          onClose={() => {
            setCreateOpen(false);
            setCreated(null);
          }}
        >
          {created ? (
            <div className="text-sm">
              <p className="text-green-300">
                Organization created — {form.name}
              </p>
              <div className="mt-3 rounded-md border border-slate-700 bg-slate-950 p-3">
                <p className="text-xs uppercase tracking-wide text-slate-500">
                  API key (shown once)
                </p>
                <p className="mt-1 break-all font-mono text-xs text-amber-200">
                  {created.api_key}
                </p>
              </div>
              <button
                onClick={() => {
                  setCreateOpen(false);
                  setCreated(null);
                }}
                className="mt-4 w-full rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
              >
                Done
              </button>
            </div>
          ) : (
            <>
              <div className="space-y-3 text-sm">
                <div>
                  <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                    Organization name *
                  </label>
                  <input
                    value={form.name}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="Acme Corp"
                    className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                    Business name
                  </label>
                  <input
                    value={form.business_name}
                    onChange={(e) =>
                      setForm({ ...form, business_name: e.target.value })
                    }
                    placeholder="Acme Corporation Inc."
                    className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                  />
                </div>
                <label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={form.is_active}
                    onChange={(e) =>
                      setForm({ ...form, is_active: e.target.checked })
                    }
                    className="accent-indigo-500"
                  />
                  <span className="text-slate-300">Active on creation</span>
                </label>
              </div>
              <div className="mt-5 flex justify-end gap-2">
                <button
                  onClick={() => setCreateOpen(false)}
                  disabled={creating}
                  className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  onClick={createOrg}
                  disabled={creating || form.name.trim().length < 2}
                  className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
                >
                  {creating ? "Creating…" : "Create"}
                </button>
              </div>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}
