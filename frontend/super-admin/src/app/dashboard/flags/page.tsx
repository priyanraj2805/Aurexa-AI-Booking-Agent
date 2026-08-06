"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { Modal, ConfirmDialog } from "@/components/modal";

type FlagRow = {
  id: string;
  code: string;
  name: string;
  description: string | null;
  enabled: boolean;
  scope: "platform" | "org" | "plan";
  targeting: Record<string, unknown>;
  rollout_percent: number;
  updated_by_id: string | null;
  created_at: string | null;
  updated_at: string | null;
};

type OrgRow = { id: string; name: string; business_name: string | null };
type PlanRow = { id: string; code: string; name: string };

const SCOPES = ["platform", "org", "plan"] as const;

const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleString() : "—";

export default function FlagsPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const [flags, setFlags] = useState<FlagRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [scopeFilter, setScopeFilter] = useState("all");

  const [editing, setEditing] = useState<FlagRow | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState({
    code: "",
    name: "",
    description: "",
    scope: "platform",
    rollout_percent: "100",
    org_ids: [] as string[],
    plan_codes: [] as string[],
  });
  const [saving, setSaving] = useState(false);

  const [orgSearch, setOrgSearch] = useState("");
  const [orgOptions, setOrgOptions] = useState<OrgRow[]>([]);
  const [planOptions, setPlanOptions] = useState<PlanRow[]>([]);
  const [deleting, setDeleting] = useState<FlagRow | null>(null);
  const [deletingBusy, setDeletingBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setFlags((await api.get<{ items: FlagRow[] }>("/flags")).items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load flags");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    void api
      .get<{ items: PlanRow[] }>("/plans")
      .then((r) => setPlanOptions(r.items))
      .catch(() => setPlanOptions([]));
  }, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm({
      code: "",
      name: "",
      description: "",
      scope: "platform",
      rollout_percent: "100",
      org_ids: [],
      plan_codes: [],
    });
    setCreateOpen(true);
  };

  const openEdit = (f: FlagRow) => {
    const t = f.targeting as { org_ids?: string[]; plan_codes?: string[] };
    setEditing(f);
    setForm({
      code: f.code,
      name: f.name,
      description: f.description ?? "",
      scope: f.scope,
      rollout_percent: String(f.rollout_percent),
      org_ids: t.org_ids ?? [],
      plan_codes: t.plan_codes ?? [],
    });
    setCreateOpen(true);
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

  const toggleOrg = (id: string) => {
    setForm((f) => ({
      ...f,
      org_ids: f.org_ids.includes(id)
        ? f.org_ids.filter((x) => x !== id)
        : [...f.org_ids, id],
    }));
  };

  const togglePlan = (code: string) => {
    setForm((f) => ({
      ...f,
      plan_codes: f.plan_codes.includes(code)
        ? f.plan_codes.filter((x) => x !== code)
        : [...f.plan_codes, code],
    }));
  };

  const saveFlag = async () => {
    setSaving(true);
    try {
      const body = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        scope: form.scope,
        targeting:
          form.scope === "org"
            ? { org_ids: form.org_ids }
            : form.scope === "plan"
              ? { plan_codes: form.plan_codes }
              : {},
        rollout_percent: Math.max(0, Math.min(100, Number(form.rollout_percent))),
      };
      if (editing) {
        await api.patch(`/flags/${editing.id}`, body);
        toast("success", `${form.name} updated`);
      } else {
        await api.post<{ flag_id: string }>("/flags", {
          code: form.code.trim(),
          ...body,
        });
        toast("success", `Flag "${form.code.trim()}" created`);
      }
      setCreateOpen(false);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const toggleFlag = async (f: FlagRow) => {
    try {
      const res = await api.post<{ enabled: boolean }>(`/flags/${f.id}/toggle`, {
        enabled: !f.enabled,
      });
      toast(
        "success",
        res.enabled
          ? `"${f.code}" is now ON (kill switch released)`
          : `"${f.code}" is now OFF (kill switch engaged)`
      );
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Toggle failed");
    }
  };

  const deleteFlag = async () => {
    if (!deleting) return;
    setDeletingBusy(true);
    try {
      await api.del(`/flags/${deleting.id}`);
      toast("success", `Flag "${deleting.code}" deleted`);
      setDeleting(null);
      void load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeletingBusy(false);
    }
  };

  const scopeBadge = (s: string) =>
    s === "platform" ? "text-indigo-300 bg-indigo-950/60" : s === "org" ? "text-amber-300 bg-amber-950/60" : "text-emerald-300 bg-emerald-950/60";

  const visible = flags?.filter((f) => scopeFilter === "all" || f.scope === scopeFilter) ?? [];

  return (
    <div>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Feature Flags</h1>
          <p className="mt-1 text-sm text-slate-400">
            DB-backed flags read by the tenant backend on every request. Toggle =
            instant kill switch; scope + rollout = targeted canary release.
          </p>
        </div>
        {can("flags.manage") && (
          <button
            onClick={openCreate}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            New flag
          </button>
        )}
      </div>

      <div className="mt-4 flex items-center gap-3">
        <select
          value={scopeFilter}
          onChange={(e) => setScopeFilter(e.target.value)}
          className="rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-sm text-slate-200 outline-none focus:border-indigo-500"
        >
          <option value="all">All scopes</option>
          <option value="platform">platform</option>
          <option value="org">org</option>
          <option value="plan">plan</option>
        </select>
        <span className="text-sm text-slate-400">{visible.length} flags</span>
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
              <th className="px-4 py-3">Flag</th>
              <th className="px-4 py-3">Scope</th>
              <th className="px-4 py-3">Rollout</th>
              <th className="px-4 py-3">Status</th>
              <th className="px-4 py-3">Updated</th>
              <th className="px-4 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800 bg-slate-900/50">
            {loading &&
              [0, 1, 2].map((i) => (
                <tr key={i}>
                  <td colSpan={6} className="px-4 py-3 text-slate-500">
                    Loading…
                  </td>
                </tr>
              ))}
            {!loading &&
              visible.map((f) => (
                <tr key={f.id} className={f.enabled ? "" : "opacity-60"}>
                  <td className="px-4 py-3">
                    <div className="font-medium text-slate-100">
                      {f.name}
                      <span className="ml-2 font-mono text-xs text-slate-500">
                        {f.code}
                      </span>
                    </div>
                    {f.description && (
                      <div className="text-xs text-slate-500">{f.description}</div>
                    )}
                    {f.scope === "org" && (
                      <div className="mt-0.5 text-xs text-slate-500">
                        {((f.targeting.org_ids as string[]) ?? []).length} org(s)
                      </div>
                    )}
                    {f.scope === "plan" && (
                      <div className="mt-0.5 text-xs text-slate-500">
                        {((f.targeting.plan_codes as string[]) ?? []).join(", ") ||
                          "no plans"}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-semibold ${scopeBadge(f.scope)}`}
                    >
                      {f.scope.toUpperCase()}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-300">
                    {f.rollout_percent}%
                  </td>
                  <td className="px-4 py-3">
                    <button
                      onClick={() => can("flags.manage") && void toggleFlag(f)}
                      disabled={!can("flags.manage")}
                      className={`relative h-6 w-11 rounded-full transition-colors disabled:opacity-40 ${
                        f.enabled ? "bg-green-600" : "bg-slate-700"
                      }`}
                      aria-label={`Toggle ${f.code}`}
                    >
                      <span
                        className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${
                          f.enabled ? "left-[22px]" : "left-0.5"
                        }`}
                      />
                    </button>
                    <span className="ml-2 text-xs text-slate-400">
                      {f.enabled ? "ON" : "OFF"}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {fmtDate(f.updated_at)}
                  </td>
                  <td className="px-4 py-3">
                    {can("flags.manage") && (
                      <div className="flex gap-2">
                        <button
                          onClick={() => openEdit(f)}
                          className="rounded border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => setDeleting(f)}
                          className="rounded border border-slate-700 px-2 py-1 text-xs text-red-400 hover:bg-red-950"
                        >
                          Delete
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            {!loading && visible.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  No flags found
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {createOpen && (
        <Modal
          title={editing ? `Edit ${editing.name}` : "New feature flag"}
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
                  placeholder="chatbot.enabled"
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
                  Scope
                </label>
                <select
                  value={form.scope}
                  onChange={(e) => setForm({ ...form, scope: e.target.value })}
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                >
                  {SCOPES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
                  Rollout %
                </label>
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={form.rollout_percent}
                  onChange={(e) => setForm({ ...form, rollout_percent: e.target.value })}
                  className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                />
              </div>
            </div>

            {form.scope === "platform" && (
              <p className="rounded-md border border-slate-800 p-2 text-xs text-slate-400">
                Applies to all organizations. Rollout &lt; 100% = canary bucket by
                org.
              </p>
            )}

            {form.scope === "org" && (
              <div className="rounded-md border border-slate-800 p-3">
                <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                  Target organizations ({form.org_ids.length} selected)
                </p>
                <div className="flex gap-2">
                  <input
                    value={orgSearch}
                    onChange={(e) => setOrgSearch(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && void searchOrgs()}
                    placeholder="Search orgs…"
                    className="flex-1 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 outline-none focus:border-indigo-500"
                  />
                  <button
                    onClick={searchOrgs}
                    className="rounded-md border border-slate-700 px-3 py-2 text-slate-300 hover:bg-slate-800"
                  >
                    Search
                  </button>
                </div>
                {orgOptions.length > 0 && (
                  <div className="mt-2 max-h-40 space-y-1 overflow-y-auto">
                    {orgOptions.map((o) => (
                      <button
                        key={o.id}
                        onClick={() => toggleOrg(o.id)}
                        className={`block w-full rounded-md border px-3 py-1.5 text-left ${
                          form.org_ids.includes(o.id)
                            ? "border-indigo-500 bg-indigo-950/50"
                            : "border-slate-800 hover:bg-slate-800"
                        }`}
                      >
                        {o.name}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {form.scope === "plan" && (
              <div className="rounded-md border border-slate-800 p-3">
                <p className="mb-2 text-xs uppercase tracking-wide text-slate-500">
                  Target plans ({form.plan_codes.length} selected)
                </p>
                <div className="space-y-1">
                  {planOptions.map((p) => (
                    <button
                      key={p.id}
                      onClick={() => togglePlan(p.code)}
                      className={`block w-full rounded-md border px-3 py-1.5 text-left ${
                        form.plan_codes.includes(p.code)
                          ? "border-indigo-500 bg-indigo-950/50"
                          : "border-slate-800 hover:bg-slate-800"
                      }`}
                    >
                      {p.name}
                      <span className="ml-2 font-mono text-xs text-slate-500">
                        {p.code}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
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
              onClick={saveFlag}
              disabled={
                saving ||
                form.name.trim().length < 2 ||
                (editing ? false : form.code.trim().length < 2) ||
                (form.scope === "org" && form.org_ids.length === 0) ||
                (form.scope === "plan" && form.plan_codes.length === 0)
              }
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              {saving ? "Saving…" : editing ? "Save changes" : "Create flag"}
            </button>
          </div>
        </Modal>
      )}

      {deleting && (
        <ConfirmDialog
          title="Delete feature flag"
          message={
            <span>
              Delete <strong>{deleting.code}</strong>? This removes it from the
              registry — the tenant backend will fall back to its default for the
              feature.
            </span>
          }
          confirmLabel="Delete"
          danger
          busy={deletingBusy}
          onConfirm={deleteFlag}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}
