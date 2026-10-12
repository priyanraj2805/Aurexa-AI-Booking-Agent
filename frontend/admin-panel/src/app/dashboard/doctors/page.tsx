"use client";

import { useEffect, useState } from "react";
import { orgApi, Provider, Unit } from "@/lib/org-api";
import { toast } from "sonner";

export default function TeamPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [name, setName] = useState("");
  const [unitId, setUnitId] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const [p, u] = await Promise.all([orgApi.providers(), orgApi.units()]);
      setProviders(p);
      setUnits(u);
      if (!unitId && u.length) setUnitId(u[0].id);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const p = await orgApi.createProvider({ full_name: name.trim() });
      if (unitId) await orgApi.assign(unitId, p.id);
      setName("");
      toast.success("Member added");
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    }
  };

  if (loading) return <p className="text-sm text-muted-foreground">Loading team…</p>;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Team</h1>
        <p className="text-sm text-muted-foreground">Salesmen in your mall — each has own time-table and bookings.</p>
      </div>
      <form onSubmit={create} className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Priya Sharma"
          className="flex-1 px-3 py-2 bg-background border border-border rounded-lg text-sm outline-none focus:border-primary"
        />
        <select value={unitId} onChange={(e) => setUnitId(e.target.value)} className="px-3 py-2 bg-background border border-border rounded-lg text-sm">
          {units.map((u) => (
            <option key={u.id} value={u.id}>{u.name}</option>
          ))}
        </select>
        <button className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold">Add</button>
      </form>
      <div className="grid gap-3">
        {providers.map((p) => (
          <div key={p.id} className="border border-border rounded-xl p-4 bg-card">
            <p className="font-medium">{p.full_name}</p>
            <p className="text-xs text-muted-foreground">
              {p.timezone || "Uses org default"} · {p.slot_duration_minutes || 60} min · {p.units.length} dept
            </p>
          </div>
        ))}
        {!providers.length && <p className="text-sm text-muted-foreground">No team members yet.</p>}
      </div>
    </div>
  );
}
