"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AuditAction, AuditChanges } from "@/components/audit/audit-change";

type AuditRow = {
  id: string;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  old_values: unknown;
  new_values: unknown;
  ip_address: string | null;
  created_at: string;
};

type ChainResult = {
  total_records: number;
  chain_valid: boolean;
  first_broken_index: number | null;
};

export default function AuditPage() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [chain, setChain] = useState<ChainResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [r, v] = await Promise.all([
        api.get<AuditRow[]>("/audit?limit=50"),
        api.get<ChainResult>("/audit/verify"),
      ]);
      setRows(r);
      setChain(v);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load audit logs");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Audit Logs</h1>
        {chain && (
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              chain.chain_valid
                ? "bg-green-900/60 text-green-300"
                : "bg-red-900/60 text-red-300"
            }`}
          >
            Chain: {chain.chain_valid ? "VALID" : "TAMPERED"} · {chain.total_records}{" "}
            records
          </span>
        )}
      </div>
      <p className="mt-1 text-sm text-slate-400">
        Append-only, hash-chained trail — updates and deletes are rejected by the
        database.
      </p>

      {error && (
        <p className="mt-4 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      <div className="mt-6 overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">Time</th>
              <th className="px-4 py-3">Actor</th>
              <th className="px-4 py-3">Action</th>
              <th className="px-4 py-3">Changes</th>
              <th className="px-4 py-3">Resource</th>
              <th className="px-4 py-3">IP</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="px-4 py-3 text-slate-300">
                  {new Date(r.created_at).toLocaleString()}
                </td>
                <td className="px-4 py-3">{r.actor_email ?? "—"}</td>
                <td className="px-4 py-3">
                  <AuditAction action={r.action} />
                </td>
                <td className="px-4 py-3">
                  <AuditChanges
                    action={r.action}
                    oldValues={r.old_values}
                    newValues={r.new_values}
                  />
                </td>
                <td className="px-4 py-3 text-slate-300">
                  {r.resource_type}
                  {r.resource_id ? ` · ${r.resource_id.slice(0, 8)}…` : ""}
                </td>
                <td className="px-4 py-3 text-slate-400">{r.ip_address}</td>
              </tr>
            ))}
            {rows.length === 0 && !error && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  No audit records yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
