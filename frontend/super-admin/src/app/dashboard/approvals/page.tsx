"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useToast } from "@/components/toast";
import { ConfirmDialog, Modal } from "@/components/modal";
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

type Approval = {
  id: string;
  request_type: string;
  resource_type: string;
  resource_id: string | null;
  payload: Record<string, unknown> | null;
  status: string;
  requested_by_id: string | null;
  requested_by_email: string | null;
  reviewed_by_id: string | null;
  reviewed_by_email: string | null;
  review_note: string | null;
  created_at: string | null;
  expires_at: string | null;
  reviewed_at: string | null;
  executed_at: string | null;
};

const STATUS_STYLES: Record<string, string> = {
  PENDING: "bg-amber-900/60 text-amber-300",
  APPROVED: "bg-indigo-900/60 text-indigo-300",
  EXECUTED: "bg-green-900/60 text-green-300",
  REJECTED: "bg-red-900/60 text-red-300",
  EXPIRED: "bg-slate-800 text-slate-400",
};

const STATUS_OPTIONS = [
  { value: "PENDING", label: "Pending" },
  { value: "APPROVED", label: "Approved" },
  { value: "EXECUTED", label: "Executed" },
  { value: "REJECTED", label: "Rejected" },
  { value: "EXPIRED", label: "Expired" },
];

const SORT_DEFAULTS: Record<string, "asc" | "desc"> = {
  created_at: "desc",
  updated_at: "desc",
  reviewed_at: "desc",
  executed_at: "desc",
  expires_at: "asc",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
        STATUS_STYLES[status] ?? "bg-slate-800 text-slate-400"
      }`}
    >
      {status}
    </span>
  );
}

export default function ApprovalsPage() {
  const { toast } = useToast();
  const [items, setItems] = useState<Approval[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [acting, setActing] = useState<Approval | null>(null);
  const [action, setAction] = useState<"approve" | "reject">("approve");
  const [note, setNote] = useState("");
  const [stepupOpen, setStepupOpen] = useState(false);
  const [stepupCode, setStepupCode] = useState("");
  const [stepupBusy, setStepupBusy] = useState(false);

  const [statusFilters, setStatusFilters] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [sortBy, setSortBy] = useState("created_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");

  const handleSort = (column: string, order: "asc" | "desc") => {
    setSortBy(column);
    setSortOrder(order);
    setPage(1);
  };

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    if (!isDateRangeValid(fromDate, toDate)) {
      setItems([]);
      setTotal(0);
      setLoading(false);
      return;
    }
    try {
      const q = new URLSearchParams();
      for (const s of statusFilters) q.append("status", s);
      if (search) q.append("search", search);
      if (fromDate) q.append("from_date", toApiDate(fromDate) || fromDate);
      if (toDate) q.append("to_date", toApiDateTo(toDate) || toDate);
      q.append("sort_by", sortBy);
      q.append("sort_order", sortOrder);
      q.append("page", String(page));
      q.append("page_size", "10");
      const res = await api.get<{ items: Approval[]; total: number }>(
        `/approvals?${q.toString()}`
      );
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load approvals");
    } finally {
      setLoading(false);
    }
  }, [statusFilters, search, fromDate, toDate, sortBy, sortOrder, page]);

  useEffect(() => {
    load();
  }, [load]);

  const submit = async () => {
    if (!acting) return;
    setBusy(true);
    try {
      await api.post(`/approvals/${acting.id}/${action}`, { note: note || null });
      toast(
        "success",
        action === "approve"
          ? "Approved — bridge action executed"
          : "Request rejected"
      );
      setActing(null);
      setNote("");
      await load();
    } catch (e) {
      const message = e instanceof Error ? e.message : "Action failed";
      if (action === "approve" && message.includes("MFA step-up required")) {
        setStepupOpen(true);
        setStepupCode("");
        return;
      }
      toast("error", message);
    } finally {
      setBusy(false);
    }
  };

  const doStepupAndApprove = async () => {
    if (!acting) return;
    setStepupBusy(true);
    try {
      await api.post("/auth/mfa/stepup", { code: stepupCode.trim() });
      await api.post(`/approvals/${acting.id}/${action}`, { note: note || null });
      toast(
        "success",
        action === "approve"
          ? "Approved — bridge action executed"
          : "Request rejected"
      );
      setStepupOpen(false);
      setStepupCode("");
      setActing(null);
      setNote("");
      await load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Verification failed");
    } finally {
      setStepupBusy(false);
    }
  };

  const pending = items.filter((i) => i.status === "PENDING");
  const history = items.filter((i) => i.status !== "PENDING");

  return (
    <div>
      <h1 className="text-2xl font-bold">Approvals</h1>
      <p className="mt-1 text-sm text-slate-400">
        Destructive actions need a second admin. Requests expire after 24 hours.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <SearchInput
          value={search}
          onChange={(v) => {
            setSearch(v);
            setPage(1);
          }}
          placeholder="Search request, email, note…"
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

      <h2 className="mt-6 text-sm font-semibold uppercase tracking-wide text-slate-400">
        Pending ({pending.length})
      </h2>
      <div className="mt-2 overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">Request</th>
              <th className="px-4 py-3">Resource</th>
              <th className="px-4 py-3">Requested by</th>
              <SortableTh
                column="expires_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.expires_at}
              >
                Expires
              </SortableTh>
              <th className="px-4 py-3"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {loading && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-500">
                  Loading…
                </td>
              </tr>
            )}
            {!loading &&
              pending.map((a) => (
                <tr key={a.id}>
                  <td className="px-4 py-3">
                    <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs">
                      {a.request_type}
                    </span>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs text-slate-300">
                    {(a.payload as { name?: string } | null)?.name ??
                      a.resource_id}
                  </td>
                  <td className="px-4 py-3 text-slate-300">
                    {a.requested_by_email ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-slate-400">
                    {a.expires_at
                      ? new Date(a.expires_at).toLocaleString()
                      : "—"}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => {
                        setActing(a);
                        setAction("approve");
                        setNote("");
                      }}
                      disabled={busy}
                      className="mr-2 rounded border border-green-700 px-2 py-1 text-xs text-green-300 hover:bg-green-950 disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      onClick={() => {
                        setActing(a);
                        setAction("reject");
                        setNote("");
                      }}
                      disabled={busy}
                      className="rounded border border-red-700 px-2 py-1 text-xs text-red-300 hover:bg-red-950 disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </td>
                </tr>
              ))}
            {!loading && pending.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  No pending approvals
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <h2 className="mt-8 text-sm font-semibold uppercase tracking-wide text-slate-400">
        History ({history.length})
      </h2>
      <div className="mt-2 overflow-x-auto rounded-lg border border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
            <tr>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Request</th>
              <th className="px-4 py-3">Requested by</th>
              <th className="px-4 py-3">Reviewed by</th>
              <th className="px-4 py-3">Note</th>
              <SortableTh
                column="created_at"
                sortBy={sortBy}
                sortOrder={sortOrder}
                onSort={handleSort}
                defaultOrder={SORT_DEFAULTS.created_at}
              >
                Created
              </SortableTh>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {history.map((a) => (
              <tr key={a.id}>
                <td className="px-4 py-3">
                  <StatusBadge status={a.status} />
                </td>
                <td className="px-4 py-3">
                  <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs">
                    {a.request_type}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-300">
                  {a.requested_by_email ?? "—"}
                </td>
                <td className="px-4 py-3 text-slate-300">
                  {a.reviewed_by_email ?? "—"}
                </td>
                <td className="px-4 py-3 text-xs text-slate-400">
                  {a.review_note ?? "—"}
                </td>
                <td className="px-4 py-3 text-slate-400">
                  {a.created_at ? new Date(a.created_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
            {history.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  No approval history yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <Pagination page={page} pageSize={10} total={total} onPageChange={setPage} />

      {acting && (
        <ConfirmDialog
          title={action === "approve" ? "Approve request" : "Reject request"}
          message={
            <>
              <p>
                {action === "approve" ? (
                  <>
                    Approving <b>{acting.request_type}</b> for{" "}
                    <b className="font-mono text-xs">{acting.resource_id}</b> will
                    execute it immediately.
                  </>
                ) : (
                  <>
                    Reject the <b>{acting.request_type}</b> request for{" "}
                    <b className="font-mono text-xs">{acting.resource_id}</b>.
                  </>
                )}
              </p>
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Review note (optional)"
                className="mt-3 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm outline-none focus:border-indigo-500"
              />
            </>
          }
          confirmLabel={action === "approve" ? "Approve & execute" : "Reject"}
          danger={action === "reject"}
          busy={busy}
          onConfirm={submit}
          onCancel={() => setActing(null)}
        />
      )}

      {stepupOpen && (
        <Modal title="MFA step-up" onClose={() => !stepupBusy && setStepupOpen(false)}>
          <p className="text-sm text-slate-400">
            Approving an org deletion requires re-verifying your identity. Enter
            your authenticator code to continue.
          </p>
          <input
            autoFocus
            value={stepupCode}
            onChange={(e) =>
              setStepupCode(e.target.value.replace(/\D/g, "").slice(0, 6))
            }
            onKeyDown={(e) => e.key === "Enter" && void doStepupAndApprove()}
            placeholder="000000"
            inputMode="numeric"
            className="mt-4 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-lg tracking-widest outline-none focus:border-indigo-500"
          />
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setStepupOpen(false)}
              disabled={stepupBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={doStepupAndApprove}
              disabled={stepupBusy || stepupCode.length < 6}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {stepupBusy ? "Verifying…" : "Verify & approve"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
