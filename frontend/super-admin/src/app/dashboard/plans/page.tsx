"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal, ConfirmDialog } from "@/components/modal";

type PlanRow = {
  id: string;
  code: string;
  name: string;
  description: string | null;
  is_active: boolean;
  monthly_price_cents: number;
  period: string;
  limits: Record<string, number | null>;
  created_at: string | null;
  updated_at: string | null;
};

type OrgRow = {
  id: string;
  name: string;
  business_name: string | null;
};

const LIMIT_FIELDS: { key: string; label: string; suffix: string }[] = [
  { key: "bookings_per_month", label: "Bookings / month", suffix: "" },
  { key: "ai_tokens_per_month", label: "AI tokens / month", suffix: "" },
  { key: "seats", label: "Seats", suffix: "" },
  { key: "storage_gb", label: "Storage", suffix: "GB" },
];

const EMPTY_LIMITS = { bookings_per_month: "", ai_tokens_per_month: "", seats: "", storage_gb: "" };
type LimitForm = typeof EMPTY_LIMITS;
const LIMIT_KEY = (k: string) => k as keyof LimitForm;

const fmtMoney = (cents: number) =>
  (cents / 100).toLocaleString("en-US", { style: "currency", currency: "USD" });

const fmtLimit = (v: number | null | undefined) =>
  v === null || v === undefined ? "Unlimited" : v.toLocaleString();

export default function PlansPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const [plans, setPlans] = useState<PlanRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showArchived, setShowArchived] = useState(false);

  const [editing, setEditing] = useState<PlanRow | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({
    code: "",
    name: "",
    description: "",
    price_dollars: "",
    period: "monthly",
    limits: { ...EMPTY_LIMITS },
  });
  const [saving, setSaving] = useState(false);

  const [archiving, setArchiving] = useState<PlanRow | null>(null);
  const [archivingBusy, setArchivingBusy] = useState(false);

  const [deleting, setDeleting] = useState<PlanRow | null>(null);
  const [deletingBusy, setDeletingBusy] = useState(false);

  const [assigning, setAssigning] = useState<PlanRow | null>(null);
  const [assignForm, setAssignForm] = useState({ org_id: "", note: "" });
  const [orgOptions, setOrgOptions] = useState<OrgRow[]>([]);
  const [orgSearch, setOrgSearch] = useState("");
  const [assigningBusy, setAssigningBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPlans((await api.get<{ items: PlanRow[] }>("/plans?include_inactive=true")).items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load plans");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({
      code: "",
      name: "",
      description: "",
      price_dollars: "",
      period: "monthly",
      limits: { ...EMPTY_LIMITS },
    });
    setCreateOpen(true);
  };

  const openEdit = (p: PlanRow) => {
    setEditing(p);
    setForm({
      code: p.code,
      name: p.name,
      description: p.description ?? "",
      price_dollars: (p.monthly_price_cents / 100).toFixed(2),
      period: p.period,
      limits: {
        bookings_per_month: p.limits.bookings_per_month?.toString() ?? "",
        ai_tokens_per_month: p.limits.ai_tokens_per_month?.toString() ?? "",
        seats: p.limits.seats?.toString() ?? "",
        storage_gb: p.limits.storage_gb?.toString() ?? "",
      },
    });
    setCreateOpen(true);
  };

  const buildLimits = (): Record<string, number | null> => {
    const out: Record<string, number | null> = {};
    for (const f of LIMIT_FIELDS) {
      const raw = form.limits[LIMIT_KEY(f.key)].trim();
      out[f.key] = raw === "" ? null : Math.max(0, Math.floor(Number(raw)));
    }
    return out;
  };

  const savePlan = async () => {
    setSaving(true);
    try {
      const body = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        monthly_price_cents: Math.round(parseFloat(form.price_dollars || "0") * 100),
        period: form.period,
        limits: buildLimits(),
      };
      if (editing) {
        await api.patch(`/plans/${editing.id}`, body);
        toast("success", `${form.name} updated`);
      } else {
        const res = await api.post<{ plan_id: string }>("/plans", {
          code: form.code.trim(),
          ...body,
        });
        toast("success", `Plan created (${res.plan_id.slice(0, 8)}…)`);
      }
      setCreateOpen(false);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const archivePlan = async () => {
    if (!archiving) return;
    setArchivingBusy(true);
    try {
      const res = await api.del<{ ok: boolean; archived: boolean }>(
        `/plans/${archiving.id}`
      );
      toast(
        "success",
        res.archived
          ? `${archiving.name} archived (still assigned to orgs)`
          : `${archiving.name} deleted`
      );
      setArchiving(null);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Archive failed");
    } finally {
      setArchivingBusy(false);
    }
  };

  const deletePlan = async () => {
    if (!deleting) return;
    setDeletingBusy(true);
    try {
      const res = await api.del<{
        ok: boolean;
        deleted: boolean;
        sunset?: boolean;
        orgs_affected?: number;
      }>(`/plans/${deleting.id}`);
      if (res.deleted) {
        toast("success", `${deleting.name} permanently deleted`);
      } else if (res.sunset) {
        toast(
          "success",
          `Plan sunset: ${res.orgs_affected ?? 0} org(s) keep it until their billing period ends, then it expires`
        );
      } else {
        toast("success", `${deleting.name} archived (still referenced)`);
      }
      setDeleting(null);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeletingBusy(false);
    }
  };

  const openAssign = (p: PlanRow) => {
    setAssigning(p);
    setAssignForm({ org_id: "", note: "" });
    setOrgSearch("");
    setOrgOptions([]);
  };

  const searchOrgs = async () => {
    try {
      const res = await api.get<{ items: OrgRow[] }>(
        `/tenants/orgs?search=${encodeURIComponent(orgSearch)}`
      );
      setOrgOptions(res.items);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Organization search failed");
    }
  };

  const doAssign = async () => {
    if (!assigning || !assignForm.org_id) return;
    setAssigningBusy(true);
    try {
      await api.post(`/plans/${assigning.id}/assign`, {
        org_id: assignForm.org_id,
        note: assignForm.note || null,
      });
      toast("success", `${assigning.name} assigned`);
      setAssigning(null);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Assign failed");
    } finally {
      setAssigningBusy(false);
    }
  };

  const visible = plans?.filter((p) => showArchived || p.is_active) ?? [];

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Plans</h1>
          <p className="mt-1 text-sm text-slate-400">
            Pricing plans and per-plan usage limits (blank limit = unlimited).
          </p>
        </div>
        {can("plans.manage") && (
          <button
            onClick={openCreate}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            New plan
          </button>
        )}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-slate-300">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
            className="accent-indigo-500"
          />
          Show archived
        </label>
        <span className="text-sm text-slate-400">{visible.length} plans</span>
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
              <th className="px-4 py-3">Plan</th>
              <th className="px-4 py-3">Price</th>
              <th className="px-4 py-3">Limits</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {loading &&
              [0, 1, 2].map((i) => (
                <tr key={i}>
                  <td colSpan={5} className="px-4 py-3 text-slate-500">
                    Loading…
                  </td>
                </tr>
              ))}
            {!loading &&
              visible.map((p) => (
                <tr key={p.id} className={p.is_active ? "" : "opacity-60"}>
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-100">
                      {p.name}
                      <span className="ml-2 font-mono text-xs text-slate-500">
                        {p.code}
                      </span>
                    </div>
                    {p.description && (
                      <div className="text-xs text-slate-500">{p.description}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {fmtMoney(p.monthly_price_cents)}
                    <div className="text-xs text-slate-500">/ {p.period}</div>
                  </td>
                  <td className="px-4 py-3 text-xs">
                    {LIMIT_FIELDS.map((f) => (
                      <div key={f.key} className="flex justify-between gap-6">
                        <span className="text-slate-500">{f.label}</span>
                        <span className="text-slate-300">
                          {fmtLimit(p.limits[f.key])}
                          {f.suffix}
                        </span>
                      </div>
                    ))}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                        p.is_active
                          ? "bg-green-900/60 text-green-300"
                          : "bg-slate-800 text-slate-400"
                      }`}
                    >
                      {p.is_active ? "ACTIVE" : "ARCHIVED"}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      {can("plans.manage") && (
                        <>
                          <button
                            onClick={() => openEdit(p)}
                            className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800"
                          >
                            Edit
                          </button>
                          {p.is_active && (
                            <button
                              onClick={() => setArchiving(p)}
                              className="rounded border border-slate-700 px-2 py-1 text-xs text-red-400 hover:bg-red-950"
                            >
                              Archive
                            </button>
                          )}
                          {!p.is_active && (
                            <button
                              onClick={() => setDeleting(p)}
                              className="rounded border border-slate-700 px-2 py-1 text-xs text-red-400 hover:bg-red-950"
                            >
                              Delete
                            </button>
                          )}
                        </>
                      )}
                      {can("plans.assign") && p.is_active && (
                        <button
                          onClick={() => openAssign(p)}
                          className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800"
                        >
                          Assign
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            {!loading && visible.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                  No plans found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {createOpen && (
        <Modal
          title={editing ? `Edit ${editing.name}` : "New plan"}
          onClose={() => setCreateOpen(false)}
        >
          <div className="space-y-3 text-sm">
            {!editing && (
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                  Code *
                </label>
                <input
                  value={form.code}
                  onChange={(e) => setForm({ ...form, code: e.target.value })}
                  placeholder="pro-2026"
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Name *
              </label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="Pro"
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Description
              </label>
              <input
                value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                  Price ($/month)
                </label>
                <input
                  value={form.price_dollars}
                  onChange={(e) => setForm({ ...form, price_dollars: e.target.value })}
                  placeholder="99.00"
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                  Period
                </label>
                <select
                  value={form.period}
                  onChange={(e) => setForm({ ...form, period: e.target.value })}
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                >
                  <option value="monthly">monthly</option>
                  <option value="annual">annual</option>
                </select>
              </div>
            </div>
            <div className="rounded-md border border-slate-800 p-3">
              <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                Limits (leave blank = unlimited)
              </p>
              <div className="grid grid-cols-2 gap-3">
                {LIMIT_FIELDS.map((f) => (
                  <div key={f.key}>
                    <label className="mb-1 block text-xs text-slate-400">
                      {f.label}
                    </label>
                    <input
                      value={form.limits[LIMIT_KEY(f.key)]}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          limits: { ...form.limits, [LIMIT_KEY(f.key)]: e.target.value },
                        })
                      }
                      placeholder="∞"
                      className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                    />
                  </div>
                ))}
              </div>
            </div>
            {editing && (
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={editing.is_active}
                  onChange={(e) => {
                    const next = { ...editing, is_active: e.target.checked };
                    setEditing(next);
                    void api
                      .patch(`/plans/${next.id}`, { is_active: next.is_active })
                      .then(() => {
                        toast("success", `${next.name} is now ${next.is_active ? "active" : "inactive"}`);
                        void load();
                      })
                      .catch((err) =>
                        toast("error", err instanceof Error ? err.message : "Status change failed")
                      );
                  }}
                  className="accent-indigo-500"
                />
                <span className="text-slate-300">Plan is active</span>
              </label>
            )}
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setCreateOpen(false)}
              disabled={saving}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={savePlan}
              disabled={saving || form.name.trim().length < 2}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? "Saving…" : editing ? "Save changes" : "Create plan"}
            </button>
          </div>
        </Modal>
      )}

      {archiving && (
        <ConfirmDialog
          title="Archive plan"
          message={
            <span>
              Archive <strong>{archiving.name}</strong>? If any org is assigned to it
              the plan is kept (archived) for those assignments.
            </span>
          }
          confirmLabel="Archive"
          danger
          busy={archivingBusy}
          onConfirm={archivePlan}
          onCancel={() => setArchiving(null)}
        />
      )}

      {deleting && (
        <ConfirmDialog
          title="Delete plan permanently"
          message={
            <span>
              Delete <strong>{deleting.name}</strong>? Organizations currently on
              this plan keep it until their billing period ends — after that the
              plan expires for them and renewals stop at this price. Plans with
              only billing history stay archived.
            </span>
          }
          confirmLabel="Delete permanently"
          danger
          busy={deletingBusy}
          onConfirm={deletePlan}
          onCancel={() => setDeleting(null)}
        />
      )}

      {assigning && (
        <Modal title={`Assign ${assigning.name}`} onClose={() => setAssigning(null)}>
          <div className="space-y-3 text-sm">
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Search organization
              </label>
              <div className="flex gap-2">
                <input
                  value={orgSearch}
                  onChange={(e) => setOrgSearch(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && void searchOrgs()}
                  placeholder="Acme Corp…"
                  className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
                <button
                  onClick={searchOrgs}
                  className="rounded-md border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800"
                >
                  Search
                </button>
              </div>
            </div>
            {orgOptions.length > 0 && (
              <div className="max-h-48 space-y-1 overflow-y-auto">
                {orgOptions.map((o) => (
                  <button
                    key={o.id}
                    onClick={() =>
                      setAssignForm({ ...assignForm, org_id: o.id, note: o.name })
                    }
                    className={`block w-full rounded-md border px-3 py-2 text-left ${
                      assignForm.org_id === o.id
                        ? "border-indigo-500 bg-indigo-950/50"
                        : "border-slate-800 hover:bg-slate-800"
                    }`}
                  >
                    <span className="text-slate-200">{o.name}</span>
                    {o.business_name && (
                      <span className="block text-xs text-slate-500">
                        {o.business_name}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            )}
            <div>
              <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                Note
              </label>
              <input
                value={assignForm.note}
                onChange={(e) => setAssignForm({ ...assignForm, note: e.target.value })}
                className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
              />
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            <button
              onClick={() => setAssigning(null)}
              disabled={assigningBusy}
              className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={doAssign}
              disabled={assigningBusy || !assignForm.org_id}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {assigningBusy ? "Assigning…" : "Assign plan"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
