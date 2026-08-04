"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal } from "@/components/modal";

type OrgRow = {
  id: string;
  name: string;
  business_name: string | null;
  is_active: boolean;
  api_key_masked: string;
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

export default function OrganizationsPage() {
  const { can } = useAuth();
  const { toast } = useToast();
  const [data, setData] = useState<OrgList | null>(null);
  const [search, setSearch] = useState("");
  const [isActive, setIsActive] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({ name: "", business_name: "", is_active: true });
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<{ org_id: string; api_key: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (isActive !== "") params.set("is_active", isActive);
      setData(await api.get<OrgList>(`/tenants/orgs?${params.toString()}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load organizations");
    } finally {
      setLoading(false);
    }
  }, [search, isActive]);

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

      <div className="mt-4 flex items-center gap-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search name or business name…"
          className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-500"
        />
        <select
          value={isActive}
          onChange={(e) => setIsActive(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-500"
        >
          <option value="">All statuses</option>
          <option value="true">Active</option>
          <option value="false">Suspended</option>
        </select>
        <span className="text-sm text-slate-400">
          {data ? `${data.total} orgs` : ""}
        </span>
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
              <th className="px-4 py-3">Organization</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Users</th>
              <th className="px-4 py-3">Bookings</th>
              <th className="px-4 py-3">Conversations</th>
              <th className="px-4 py-3">API Key</th>
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
                  <td className="px-4 py-3 font-mono text-xs text-slate-400">
                    {o.api_key_masked}
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
