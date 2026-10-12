"use client";

import { useEffect, useState } from "react";
import { orgApi, Provider } from "@/lib/org-api";
import { api } from "@/lib/api";
import { toast } from "sonner";

interface Day {
  day_of_week: number;
  is_active: boolean;
  open_time?: string | null;
  close_time?: string | null;
  is_override?: boolean;
}

const NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default function SchedulesPage() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [pid, setPid] = useState("");
  const [days, setDays] = useState<Day[]>([]);
  const [tz, setTz] = useState("");
  const [dur, setDur] = useState("");

  const loadProviders = async () => {
    const p = await orgApi.providers();
    setProviders(p);
    if (!pid && p.length) setPid(p[0].id);
  };

  const loadDays = async (id: string) => {
    if (!id) return;
    const d = await api.get<Day[]>(`/catalog/providers/${id}/schedule`);
    setDays(d);
    const p = providers.find((x) => x.id === id);
    setTz(p?.timezone || "");
    setDur(p?.slot_duration_minutes ? String(p.slot_duration_minutes) : "");
  };

  useEffect(() => {
    loadProviders().catch((e) => toast.error(e instanceof Error ? e.message : "Failed"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (pid) loadDays(pid).catch((e) => toast.error(e instanceof Error ? e.message : "Failed"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  const save = async () => {
    try {
      await api.patch(`/catalog/providers/${pid}`, {
        timezone: tz.trim() || null,
        slot_duration_minutes: dur ? Number(dur) : null,
      });
      await api.put(`/catalog/providers/${pid}/schedule`, days);
      toast.success("Time-table saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Member time-tables</h1>
        <p className="text-sm text-muted-foreground">Each member&apos;s opening board. Empty = follows organization default.</p>
      </div>
      <select value={pid} onChange={(e) => setPid(e.target.value)} className="px-3 py-2 bg-background border border-border rounded-lg text-sm">
        {providers.map((p) => (
          <option key={p.id} value={p.id}>{p.full_name}</option>
        ))}
      </select>
      <div className="flex gap-2">
        <input value={tz} onChange={(e) => setTz(e.target.value)} placeholder="Timezone, e.g. Asia/Kolkata" className="flex-1 px-3 py-2 bg-background border border-border rounded-lg text-sm" />
        <input value={dur} onChange={(e) => setDur(e.target.value)} placeholder="Minutes, e.g. 30" className="w-40 px-3 py-2 bg-background border border-border rounded-lg text-sm" />
      </div>
      <div className="grid gap-2">
        {days.map((d, i) => (
          <div key={d.day_of_week} className="flex items-center gap-2 border border-border rounded-lg p-2 bg-card text-sm">
            <span className="w-12 font-medium">{NAMES[d.day_of_week]}</span>
            <input type="checkbox" checked={d.is_active} onChange={(e) => setDays(days.map((x, j) => (j === i ? { ...x, is_active: e.target.checked } : x)))} />
            <input value={d.open_time || ""} onChange={(e) => setDays(days.map((x, j) => (j === i ? { ...x, open_time: e.target.value } : x)))} placeholder="10:00" className="w-20 px-2 py-1 bg-background border border-border rounded text-sm" />
            <span>to</span>
            <input value={d.close_time || ""} onChange={(e) => setDays(days.map((x, j) => (j === i ? { ...x, close_time: e.target.value } : x)))} placeholder="14:00" className="w-20 px-2 py-1 bg-background border border-border rounded text-sm" />
            {d.is_override && <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">custom</span>}
          </div>
        ))}
      </div>
      <button onClick={save} className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold">Save time-table</button>
    </div>
  );
}
