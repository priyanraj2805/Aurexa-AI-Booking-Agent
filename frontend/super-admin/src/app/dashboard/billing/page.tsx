"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal } from "@/components/modal";

type Revenue = {
  mrr_cents: number;
  arr_cents: number;
  active_subscriptions: number;
  canceled_30d: number;
  churn_rate_pct: number;
  invoice_aging: {
    "0_30_days": number;
    "31_60_days": number;
    "61_90_days": number;
    "90_plus_days": number;
    open_total_cents: number;
  };
  generated_at: string;
};

type SubscriptionRow = {
  id: string;
  org_id: string;
  org_name: string;
  plan_id: string;
  plan_code: string;
  plan_name: string;
  provider: string;
  status: string;
  current_period_start: string | null;
  current_period_end: string | null;
  cancel_at_period_end: boolean;
  canceled_at: string | null;
  created_at: string | null;
};

type InvoiceRow = {
  id: string;
  org_id: string;
  org_name: string;
  number: string;
  amount_cents: number;
  currency: string;
  status: string;
  invoice_url: string | null;
  description: string | null;
  due_at: string | null;
  paid_at: string | null;
  refunded_at: string | null;
  refund_amount_cents: number;
  created_at: string | null;
};

type OrgRow = { id: string; name: string; business_name: string | null };
type PlanRow = { id: string; code: string; name: string; is_active: boolean };

type Tab = "revenue" | "subscriptions" | "invoices";

const fmtMoney = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });

const STATUS_BADGE: Record<string, string> = {
  active: "bg-green-900/60 text-green-300",
  trialing: "bg-cyan-900/60 text-cyan-300",
  past_due: "bg-amber-900/60 text-amber-300",
  canceled: "bg-slate-800 text-slate-400",
  open: "bg-amber-900/60 text-amber-300",
  paid: "bg-green-900/60 text-green-300",
  refunded: "bg-indigo-900/60 text-indigo-300",
  void: "bg-slate-800 text-slate-400",
  uncollectible: "bg-red-900/60 text-red-300",
};

const badge = (status: string) =>
  STATUS_BADGE[status] ?? "bg-slate-800 text-slate-400";

export default function BillingPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const [tab, setTab] = useState<Tab>("revenue");

  const [revenue, setRevenue] = useState<Revenue | null>(null);
  const [subs, setSubs] = useState<SubscriptionRow[]>([]);
  const [invoices, setInvoices] = useState<InvoiceRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // org picker (shared by subscribe/issue/refund modals)
  const [orgOptions, setOrgOptions] = useState<OrgRow[]>([]);
  const [orgSearch, setOrgSearch] = useState("");

  const [subOpen, setSubOpen] = useState(false);
  const [subForm, setSubForm] = useState({ org_id: "", plan_id: "" });
  const [planOptions, setPlanOptions] = useState<PlanRow[]>([]);
  const [subBusy, setSubBusy] = useState(false);

  const [issueOpen, setIssueOpen] = useState(false);
  const [issueForm, setIssueForm] = useState({ org_id: "", amount: "", description: "" });
  const [issueBusy, setIssueBusy] = useState(false);

  const [refunding, setRefunding] = useState<InvoiceRow | null>(null);
  const [refundForm, setRefundForm] = useState({ reason: "", mfa_code: "" });
  const [refundBusy, setRefundBusy] = useState(false);

  const [retryingId, setRetryingId] = useState<string | null>(null);

  const [cancelTarget, setCancelTarget] = useState<SubscriptionRow | null>(null);
  const [cancelBusy, setCancelBusy] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rev, s, inv] = await Promise.all([
        api.get<Revenue>("/billing/reports/revenue"),
        api.get<{ items: SubscriptionRow[] }>("/billing/subscriptions"),
        api.get<{ items: InvoiceRow[] }>("/billing/invoices"),
      ]);
      setRevenue(rev);
      setSubs(s.items);
      setInvoices(inv.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load billing data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  const searchOrgs = async (query: string) => {
    try {
      const res = await api.get<{ items: OrgRow[] }>(
        `/tenants/orgs?search=${encodeURIComponent(query)}`
      );
      setOrgOptions(res.items);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Organization search failed");
    }
  };

  const createSubscription = async () => {
    setSubBusy(true);
    try {
      await api.post("/billing/subscriptions", {
        org_id: subForm.org_id,
        plan_id: subForm.plan_id,
      });
      toast("success", "Subscription created");
      setSubOpen(false);
      void loadAll();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Subscription failed");
    } finally {
      setSubBusy(false);
    }
  };

  const cancelSubscription = async () => {
    if (!cancelTarget) return;
    setCancelBusy(true);
    try {
      await api.post(`/billing/subscriptions/${cancelTarget.id}/cancel`);
      toast("success", "Subscription canceled");
      setCancelTarget(null);
      void loadAll();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Cancel failed");
    } finally {
      setCancelBusy(false);
    }
  };

  const openSubscribe = async () => {
    setSubForm({ org_id: "", plan_id: "" });
    setOrgOptions([]);
    setOrgSearch("");
    setSubOpen(true);
    try {
      const res = await api.get<{ items: PlanRow[] }>("/plans");
      setPlanOptions(res.items.filter((p) => p.is_active));
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Failed to load plans");
    }
  };

  const issueInvoice = async () => {
    setIssueBusy(true);
    try {
      const res = await api.post<{ invoice_id: string }>(
        `/billing/invoices?org_id=${subForm.org_id || issueForm.org_id}`,
        {
          amount_cents: Math.round(parseFloat(issueForm.amount || "0") * 100),
          description: issueForm.description || null,
        }
      );
      toast("success", `Invoice issued (${res.invoice_id.slice(0, 8)}…)`);
      setIssueOpen(false);
      void loadAll();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Invoice failed");
    } finally {
      setIssueBusy(false);
    }
  };

  const retryInvoice = async (inv: InvoiceRow) => {
    setRetryingId(inv.id);
    try {
      await api.post(`/billing/invoices/${inv.id}/retry`);
      toast("success", `${inv.number} marked paid`);
      void loadAll();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Retry failed");
    } finally {
      setRetryingId(null);
    }
  };

  const openRefund = (inv: InvoiceRow) => {
    setRefunding(inv);
    setRefundForm({ reason: "", mfa_code: "" });
  };

  const doRefund = async () => {
    if (!refunding) return;
    setRefundBusy(true);
    try {
      if (!refundForm.mfa_code.trim()) {
        throw new Error("Enter your TOTP code (re-verifies your session for 5 min)");
      }
      await api.post("/auth/mfa/stepup", { code: refundForm.mfa_code.trim() });
      const res = await api.post<{ status: string; approval_id?: string }>(
        `/billing/invoices/${refunding.id}/refund`,
        { reason: refundForm.reason.trim() || "refund" }
      );
      if (res.status === "PENDING_APPROVAL") {
        toast(
          "success",
          "Refund is above the approval threshold — submitted. A second admin must approve it."
        );
      } else {
        toast("success", `Refund processed — ${refunding.number} refunded`);
      }
      setRefunding(null);
      void loadAll();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Refund failed");
    } finally {
      setRefundBusy(false);
    }
  };

  const aging = revenue?.invoice_aging;

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Billing</h1>
          <p className="mt-1 text-sm text-slate-400">
            Subscriptions, invoices and revenue reports (mock provider unless
            Stripe is configured).
          </p>
        </div>
        {can("billing.manage") && (
          <div className="flex gap-2">
            <button
              onClick={openSubscribe}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
            >
              Create subscription
            </button>
            <button
              onClick={() => {
                setIssueForm({ org_id: "", amount: "", description: "" });
                setOrgOptions([]);
                setOrgSearch("");
                setIssueOpen(true);
              }}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800"
            >
              Issue invoice
            </button>
          </div>
        )}
      </div>

      <div className="mt-4 flex gap-1 border-b border-slate-800">
        {(
          [
            ["revenue", "Revenue"],
            ["subscriptions", "Subscriptions"],
            ["invoices", "Invoices"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-t-md px-4 py-2 text-sm font-medium ${
              tab === key
                ? "border border-b-0 border-slate-800 bg-slate-900 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      {tab === "revenue" && (
        <div className="mt-6">
          {loading && <p className="text-sm text-slate-500">Loading…</p>}
          {!loading && revenue && (
            <>
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
                  <p className="text-xs uppercase tracking-wide text-slate-500">MRR</p>
                  <p className="mt-1 text-2xl font-bold text-slate-100">
                    {fmtMoney(revenue.mrr_cents)}
                  </p>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
                  <p className="text-xs uppercase tracking-wide text-slate-500">ARR</p>
                  <p className="mt-1 text-2xl font-bold text-slate-100">
                    {fmtMoney(revenue.arr_cents)}
                  </p>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
                  <p className="text-xs uppercase tracking-wide text-slate-500">
                    Active subscriptions
                  </p>
                  <p className="mt-1 text-2xl font-bold text-slate-100">
                    {revenue.active_subscriptions}
                  </p>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
                  <p className="text-xs uppercase tracking-wide text-slate-500">
                    Churn (30d)
                  </p>
                  <p className="mt-1 text-2xl font-bold text-slate-100">
                    {revenue.churn_rate_pct}%
                    <span className="ml-2 text-sm font-normal text-slate-500">
                      ({revenue.canceled_30d} canceled)
                    </span>
                  </p>
                </div>
              </div>

              <div className="mt-6 grid gap-4 lg:grid-cols-2">
                <div className="rounded-lg border border-slate-800">
                  <div className="border-b border-slate-800 bg-slate-900 px-4 py-3 text-sm font-medium text-slate-200">
                    Invoice aging (open)
                  </div>
                  <table className="w-full text-left text-sm">
                    <tbody className="divide-y divide-slate-800 bg-slate-900/50">
                      {(
                        [
                          ["0–30 days", aging?.["0_30_days"] ?? 0],
                          ["31–60 days", aging?.["31_60_days"] ?? 0],
                          ["61–90 days", aging?.["61_90_days"] ?? 0],
                          ["90+ days", aging?.["90_plus_days"] ?? 0],
                        ] as [string, number][]
                      ).map(([label, count]) => (
                        <tr key={label}>
                          <td className="px-4 py-2.5 text-slate-400">{label}</td>
                          <td className="px-4 py-2.5 text-right text-slate-200">{count}</td>
                        </tr>
                      ))}
                      <tr className="bg-slate-900">
                        <td className="px-4 py-2.5 font-medium text-slate-200">
                          Total open
                        </td>
                        <td className="px-4 py-2.5 text-right font-medium text-amber-300">
                          {fmtMoney(aging?.open_total_cents ?? 0)}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div className="rounded-lg border border-slate-800 p-4 text-sm text-slate-400">
                  <p className="font-medium text-slate-200">CSV export</p>
                  <p className="mt-1">
                    Download the full revenue report as CSV for spreadsheets or
                    finance tooling.
                  </p>
                  <a
                    href="/api/v1/billing/reports/revenue.csv"
                    className="mt-3 inline-block rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
                  >
                    Download revenue.csv
                  </a>
                  <p className="mt-3 text-xs text-slate-600">
                    Generated {new Date(revenue.generated_at).toLocaleString()}
                  </p>
                </div>
              </div>
            </>
          )}
        </div>
      )}

      {tab === "subscriptions" && (
        <div className="mt-6 overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-3">Organization</th>
                <th className="px-4 py-3">Plan</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Period</th>
                <th className="px-4 py-3">Provider</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-900/50">
              {loading && (
                <tr>
                  <td colSpan={6} className="px-4 py-3 text-slate-500">Loading…</td>
                </tr>
              )}
              {!loading &&
                subs.map((s) => (
                  <tr key={s.id}>
                    <td className="px-4 py-3 font-medium text-slate-200">{s.org_name}</td>
                    <td className="px-4 py-3">
                      {s.plan_name}
                      <div className="text-xs text-slate-500">{s.plan_code}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${badge(s.status)}`}>
                        {s.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400">
                      {s.current_period_start
                        ? `${new Date(s.current_period_start).toLocaleDateString()} → ${s.current_period_end ? new Date(s.current_period_end).toLocaleDateString() : "—"}`
                        : "—"}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-500">{s.provider}</td>
                    <td className="px-4 py-3">
                      {s.status === "active" && can("billing.manage") && (
                        <button
                          onClick={() => setCancelTarget(s)}
                          disabled={cancelBusy}
                          className="rounded-md border border-red-600 bg-red-950/50 px-3 py-1 text-xs text-red-300 hover:bg-red-950 disabled:opacity-50"
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              {!loading && subs.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                    No subscriptions
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === "invoices" && (
        <div className="mt-6 overflow-x-auto rounded-lg border border-slate-800">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-slate-800 bg-slate-900 text-xs uppercase tracking-wide text-slate-400">
              <tr>
                <th className="px-4 py-3">Invoice</th>
                <th className="px-4 py-3">Organization</th>
                <th className="px-4 py-3">Amount</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Paid / refunded</th>
                <th className="px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800 bg-slate-900/50">
              {loading && (
                <tr>
                  <td colSpan={6} className="px-4 py-3 text-slate-500">Loading…</td>
                </tr>
              )}
              {!loading &&
                invoices.map((inv) => (
                  <tr key={inv.id}>
                    <td className="px-4 py-3">
                      <span className="font-mono text-slate-300">{inv.number}</span>
                      {inv.description && (
                        <div className="text-xs text-slate-500">{inv.description}</div>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-300">{inv.org_name}</td>
                    <td className="px-4 py-3 font-medium text-slate-100">
                      {fmtMoney(inv.amount_cents)}
                      {inv.refund_amount_cents > 0 && (
                        <div className="text-xs font-normal text-indigo-400">
                          −{fmtMoney(inv.refund_amount_cents)} refunded
                        </div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${badge(inv.status)}`}>
                        {inv.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-400">
                      {inv.paid_at
                        ? `Paid ${new Date(inv.paid_at).toLocaleString()}`
                        : inv.refunded_at
                          ? `Refunded ${new Date(inv.refunded_at).toLocaleString()}`
                          : "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex gap-2">
                        {inv.status === "open" && can("billing.retry_payment") && (
                          <button
                            onClick={() => void retryInvoice(inv)}
                            disabled={retryingId === inv.id}
                            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                          >
                            {retryingId === inv.id ? "Retrying…" : "Mark paid"}
                          </button>
                        )}
                        {inv.status === "paid" && can("billing.refund") && (
                          <button
                            onClick={() => openRefund(inv)}
                            className="rounded border border-slate-700 px-2 py-1 text-xs text-amber-300 hover:bg-amber-950"
                          >
                            Refund
                          </button>
                        )}
                        {inv.invoice_url && (
                          <a
                            href={inv.invoice_url}
                            target="_blank"
                            rel="noreferrer"
                            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800"
                          >
                            View
                          </a>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              {!loading && invoices.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                    No invoices
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {subOpen && (
        <Modal title="Create subscription" onClose={() => setSubOpen(false)}>
          <div className="space-y-3 text-sm">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Search organization
              </label>
              <div className="flex gap-2">
                <input
                  value={orgSearch}
                  onChange={(e) => setOrgSearch(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void searchOrgs(orgSearch)}
                  placeholder="Acme Corp…"
                  className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
                <button
                  onClick={() => void searchOrgs(orgSearch)}
                  className="rounded-md border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800"
                >
                  Search
                </button>
              </div>
            </div>
            {orgOptions.length > 0 && (
              <div className="max-h-40 space-y-1 overflow-y-auto">
                {orgOptions.map((o) => (
                  <button
                    key={o.id}
                    onClick={() => setSubForm({ ...subForm, org_id: o.id })}
                    className={`block w-full rounded-md border px-3 py-2 text-left ${
                      subForm.org_id === o.id
                        ? "border-indigo-500 bg-indigo-950/50"
                        : "border-slate-800 hover:bg-slate-800"
                    }`}
                  >
                    {o.name}
                  </button>
                ))}
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Plan
              </label>
              <select
                value={subForm.plan_id}
                onChange={(e) => setSubForm({ ...subForm, plan_id: e.target.value })}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              >
                <option value="">Select plan…</option>
                {planOptions.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.code})
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setSubOpen(false)}
              disabled={subBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={createSubscription}
              disabled={subBusy || !subForm.org_id || !subForm.plan_id}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {subBusy ? "Creating…" : "Subscribe"}
            </button>
          </div>
        </Modal>
      )}

      {issueOpen && (
        <Modal title="Issue invoice" onClose={() => setIssueOpen(false)}>
          <div className="space-y-3 text-sm">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Search organization
              </label>
              <div className="flex gap-2">
                <input
                  value={orgSearch}
                  onChange={(e) => setOrgSearch(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void searchOrgs(orgSearch)}
                  placeholder="Acme Corp…"
                  className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
                <button
                  onClick={() => void searchOrgs(orgSearch)}
                  className="rounded-md border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800"
                >
                  Search
                </button>
              </div>
            </div>
            {orgOptions.length > 0 && (
              <div className="max-h-40 space-y-1 overflow-y-auto">
                {orgOptions.map((o) => (
                  <button
                    key={o.id}
                    onClick={() => setIssueForm({ ...issueForm, org_id: o.id })}
                    className={`block w-full rounded-md border px-3 py-2 text-left ${
                      issueForm.org_id === o.id
                        ? "border-indigo-500 bg-indigo-950/50"
                        : "border-slate-800 hover:bg-slate-800"
                    }`}
                  >
                    {o.name}
                  </button>
                ))}
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Amount ($)
              </label>
              <input
                value={issueForm.amount}
                onChange={(e) => setIssueForm({ ...issueForm, amount: e.target.value })}
                placeholder="99.00"
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Description
              </label>
              <input
                value={issueForm.description}
                onChange={(e) =>
                  setIssueForm({ ...issueForm, description: e.target.value })
                }
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setIssueOpen(false)}
              disabled={issueBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={issueInvoice}
              disabled={issueBusy || !issueForm.org_id || !issueForm.amount}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {issueBusy ? "Issuing…" : "Issue invoice"}
            </button>
          </div>
        </Modal>
      )}

      {refunding && (
        <Modal
          title={`Refund ${refunding.number} — ${fmtMoney(refunding.amount_cents)}`}
          onClose={() => setRefunding(null)}
        >
          <div className="space-y-3 text-sm">
            <p className="text-slate-400">
              Refunds re-verify your identity with a TOTP code. Amounts above{" "}
              <strong className="text-slate-200">$500</strong> additionally require a
              second admin&apos;s approval.
            </p>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Reason
              </label>
              <input
                value={refundForm.reason}
                onChange={(e) => setRefundForm({ ...refundForm, reason: e.target.value })}
                placeholder="Customer requested cancellation"
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Authenticator code *
              </label>
              <input
                value={refundForm.mfa_code}
                onChange={(e) =>
                  setRefundForm({ ...refundForm, mfa_code: e.target.value.replace(/\D/g, "") })
                }
                placeholder="000000"
                maxLength={6}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono tracking-widest outline-none focus:border-indigo-500"
              />
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setRefunding(null)}
              disabled={refundBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={doRefund}
              disabled={refundBusy || refundForm.mfa_code.length < 6}
              className="rounded-md bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
            >
              {refundBusy ? "Processing…" : "Refund"}
            </button>
          </div>
        </Modal>
      )}

      {cancelTarget && (
        <Modal
          title="Cancel subscription"
          onClose={() => setCancelTarget(null)}
        >
          <p className="text-slate-300">
            Cancel <strong className="text-slate-100">{cancelTarget.org_name}</strong>'s{" "}
            <strong className="text-slate-100">{cancelTarget.plan_name}</strong> subscription
            ({cancelTarget.provider}) immediately?
          </p>
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setCancelTarget(null)}
              disabled={cancelBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={cancelSubscription}
              disabled={cancelBusy}
              className="rounded-md bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
            >
              {cancelBusy ? "Canceling…" : "Yes, cancel subscription"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
