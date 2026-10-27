"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type ImpStatus = {
  active: boolean;
  user_email?: string;
  org_id?: string;
  expires_at?: string;
};

/**
 * Phase 11b banner shown while the tenant session is an impersonation one.
 * Lets the org admin (or support agent) exit instantly and reminds them the
 * session is audited. Mutations are additionally blocked server-side.
 */
export function ImpersonationBanner() {
  const [status, setStatus] = useState<ImpStatus | null>(null);
  const [exiting, setExiting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const wasActive = useRef(false);

  const load = useCallback(async () => {
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";
    try {
      const res = await fetch(`${API_BASE}/auth/impersonate/status`, {
        credentials: "include",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!res.ok) {
        setStatus({ active: false });
        return;
      }
      const data = (await res.json()) as ImpStatus;
      setStatus(data);
    } catch {
      setStatus({ active: false });
    }
  }, []);

  // Detect a remote end (admin "End now" / expiry): show a notice instead of
  // the banner silently vanishing.
  useEffect(() => {
    if (!status) return;
    if (status.active) {
      wasActive.current = true;
      return;
    }
    if (wasActive.current && !notice) {
      setNotice("Impersonation session ended.");
      const t = setTimeout(() => {
        window.location.href = "/";
      }, 2500);
      return () => clearTimeout(t);
    }
  }, [status, notice]);

  useEffect(() => {
    void load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const exit = async () => {
    if (exiting) return;
    setExiting(true);
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";
    try {
      await fetch(`${API_BASE}/auth/impersonate/exit`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
      });
    } catch {
      // best-effort; cookie clears anyway
    }
    setExiting(false);
    setNotice("Impersonation session ended. Redirecting…");
    setTimeout(() => {
      window.location.href = "/";
    }, 2500);
  };

  if (notice) {
    return (
      <div
        className="border-b border-amber-700 bg-amber-950/90 px-4 py-2 text-sm text-amber-100 backdrop-blur sm:px-6"
        role="status"
      >
        <span className="inline-flex items-center gap-2">
          <span className="h-2 w-2 shrink-0 rounded-full bg-amber-400" />
          {notice}
        </span>
      </div>
    );
  }

  if (!status?.active) return null;

  return (
    <div
      className="flex flex-wrap items-center justify-between gap-2 border-b border-red-800 bg-red-950/90 px-4 py-2 text-sm text-red-100 backdrop-blur sm:px-6"
      role="status"
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-red-500" />
        <span className="truncate">
          <strong>Impersonation session</strong> — you are viewing this account
          as {status.user_email}. Every action is audited.
        </span>
      </div>
      <button
        onClick={exit}
        disabled={exiting}
        className="shrink-0 rounded-md border border-red-700 bg-red-900 px-3 py-2 font-medium hover:bg-red-800 disabled:opacity-50"
      >
        {exiting ? "Exiting…" : "Exit impersonation"}
      </button>
    </div>
  );
}