"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";

type ActiveImpersonation = {
  id: string;
  org_name: string;
  user_email: string;
  started_at: string;
  expires_at: string;
};

export function ImpersonationBanner() {
  const { can } = useAuth();
  const { toast } = useToast();
  const [active, setActive] = useState<ActiveImpersonation | null>(null);
  const [stopping, setStopping] = useState(false);

  const load = useCallback(async () => {
    if (!can("support.impersonate")) return;
    try {
      const r = await api.get<ActiveImpersonation | null>("/impersonation/active");
      setActive(r);
    } catch {
      setActive(null);
    }
  }, [can]);

  useEffect(() => {
    void load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  const stop = async () => {
    if (!active || stopping) return;
    setStopping(true);
    try {
      await api.post(`/impersonation/${active.id}/stop`);
      toast("success", "Impersonation ended");
      setActive(null);
      window.dispatchEvent(new Event("impersonation:changed"));
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Stop failed");
    } finally {
      setStopping(false);
    }
  };

  if (!active) return null;

  const expires = new Date(active.expires_at);
  const now = Date.now();
  const minsLeft = Math.max(0, Math.round((expires.getTime() - now) / 60000));

  return (
    <div
      className="fixed inset-x-0 top-0 z-50 flex items-center justify-between gap-4 border-b border-red-800 bg-red-950/90 px-6 py-2 text-sm text-red-100 backdrop-blur"
      role="status"
    >
      <div className="flex items-center gap-2">
        <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
        <span className="font-semibold">
          You are impersonating {active.user_email}
        </span>
        <span className="text-red-300">
          at {active.org_name} — token expires in ~{minsLeft}m
        </span>
      </div>
      <button
        onClick={stop}
        disabled={stopping}
        className="rounded-md border border-red-700 bg-red-900 px-3 py-1 font-medium hover:bg-red-800 disabled:opacity-50"
      >
        {stopping ? "Ending…" : "Stop impersonation"}
      </button>
    </div>
  );
}
