"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth, notifyAuthChange } from "@/lib/auth-context";
import { apiFetch } from "@/lib/api";
import { Loader2 } from "lucide-react";

// OAuth-style callback: swaps the single-use ?code= in the URL for the
// HttpOnly session cookie, then strips the code via clean dashboard push.
export default function AuthCallbackPage() {
  const router = useRouter();
  const { refresh } = useAuth();
  const [error, setError] = useState("");

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get("code");
    if (!code) {
      router.replace("/login");
      return;
    }
    (async () => {
      try {
        const data = await apiFetch<{ user: { org_id: string } }>("/auth/code/exchange", {
          method: "POST",
          body: JSON.stringify({ code }),
          skipAuth: true,
        });
        await refresh();
        notifyAuthChange();
        router.replace("/dashboard");
      } catch {
        setError("This sign-in link expired or was already used. Please sign in again.");
        setTimeout(() => router.replace("/login"), 2500);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="w-full max-w-[420px]">
      <div className="bg-[var(--bg-card,#121212)] border border-[var(--border,#27272a)] rounded-2xl p-8 shadow-2xl text-center">
        {error ? (
          <p className="text-sm text-red-400">{error}</p>
        ) : (
          <p className="text-sm text-[var(--text-secondary,#a1a1aa)] flex items-center justify-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" /> Completing sign-in…
          </p>
        )}
      </div>
    </div>
  );
}
