"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";

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

export default function UsersPage() {
  const { can } = useAuth();
  const [data, setData] = useState<UserList | null>(null);
  const [search, setSearch] = useState("");
  const [unmask, setUnmask] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const canUnmask = can("pii.unmask");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (unmask && canUnmask) params.set("unmask", "true");
      setData(await api.get<UserList>(`/tenants/users?${params.toString()}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load users");
    } finally {
      setLoading(false);
    }
  }, [search, unmask, canUnmask]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <h1 className="text-2xl font-bold">Users</h1>
      <p className="mt-1 text-sm text-slate-400">
        Cross-organization user search. Emails and names are masked by default.
      </p>

      <div className="mt-4 flex items-center gap-3">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search email or full name…"
          className="rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm outline-none focus:border-indigo-500"
        />
        <span className="text-sm text-slate-400">
          {data ? `${data.total} users` : ""}
        </span>
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
              <th className="px-4 py-3">Name</th>
              <th className="px-4 py-3">Email</th>
              <th className="px-4 py-3">Organization</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Joined</th>
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
                  <td className="px-4 py-3 font-medium">{u.full_name ?? "—"}</td>
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
    </div>
  );
}
