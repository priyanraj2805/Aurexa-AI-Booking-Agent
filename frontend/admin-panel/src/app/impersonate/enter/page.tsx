"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, AlertCircle, CheckCircle2 } from "lucide-react";

/**
 * Phase 11b hand-off: the super admin lands here with ?hs=<handshake> after
 * clicking "Open console" in the control plane. This page exchanges the
 * handshake for a tenant session cookie, then drops into /dashboard.
 */
export default function ImpersonateEnterPage() {
  return (
    <Suspense fallback={null}>
      <ImpersonateEnterInner />
    </Suspense>
  );
}

function ImpersonateEnterInner() {
  const searchParams = useSearchParams();
  const [state, setState] = useState<"exchanging" | "error" | "done">(
    "exchanging"
  );
  const [error, setError] = useState("");
  const done = useRef(false);

  useEffect(() => {
    const hs = searchParams.get("hs");
    if (!hs) {
      setState("error");
      setError("Missing impersonation handshake.");
      return;
    }
    if (done.current) return;
    done.current = true;

    (async () => {
      const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";
      try {
        const res = await fetch(`${API_BASE}/auth/impersonate/enter`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
          },
          credentials: "include",
          body: JSON.stringify({ hs }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(
            body?.detail || `Enter failed (${res.status})`
          );
        }
        setState("done");
        // Full page load (not client-side router.replace) so the AuthProvider
        // remounts and re-validates /auth/me with the freshly set
        // impersonation ds_token cookie. A client-side navigation would keep
        // the stale "not authenticated" state and bounce to /login.
        window.setTimeout(() => window.location.replace("/dashboard"), 900);
      } catch (e) {
        setState("error");
        setError(e instanceof Error ? e.message : "Enter failed");
      }
    })();
  }, [searchParams]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#0a0a0a] px-6">
      <div className="w-full max-w-md rounded-2xl border border-zinc-800 bg-zinc-900/80 p-8 text-center">
        {state === "exchanging" && (
          <>
            <Loader2 className="mx-auto size-10 animate-spin text-[#FF6B00]" />
            <h1 className="mt-5 text-lg font-semibold text-white">
              Opening admin session…
            </h1>
            <p className="mt-2 text-sm text-zinc-400">
              Exchanging the impersonation handshake for your session.
            </p>
          </>
        )}

        {state === "done" && (
          <>
            <CheckCircle2 className="mx-auto h-10 w-10 text-green-500" />
            <h1 className="mt-5 text-lg font-semibold text-white">
              Session started
            </h1>
            <p className="mt-2 text-sm text-zinc-400">
              Redirecting you to the dashboard. Every action is audited.
            </p>
          </>
        )}

        {state === "error" && (
          <>
            {error.includes("401") ||
            error.toLowerCase().includes("expired") ? (
              <AlertCircle className="mx-auto h-10 w-10 text-amber-500" />
            ) : (
              <AlertCircle className="mx-auto h-10 w-10 text-red-500" />
            )}
            <h1 className="mt-5 text-lg font-semibold text-white">
              Could not open the session
            </h1>
            <p className="mt-2 text-sm text-zinc-400">{error}</p>
            <button
              onClick={() => window.location.replace("/")}
              className="mt-6 rounded-lg bg-[#FF6B00] px-4 py-2 text-sm font-semibold text-white hover:bg-[#e55f00]"
            >
              Back to login
            </button>
          </>
        )}
      </div>
    </div>
  );
}