"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { orgApi } from "@/lib/org-api";

function AcceptInviteForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [token, setToken] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState("");
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    const t = searchParams.get("token");
    if (t) setToken(t);
  }, [searchParams]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setDone("");
    if (!token.trim() || !fullName.trim() || password.length < 6) {
      setError("Invite link, your name and a 6+ character password are required.");
      return;
    }
    setIsLoading(true);
    try {
      await orgApi.accept(token.trim(), fullName.trim(), password);
      setDone("Account created! Redirecting to sign in…");
      setTimeout(() => router.push("/login?registered=1"), 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invite failed — the link may be expired or already used.");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 bg-[var(--bg-primary,#0a0a0a)]">
      <div className="w-full max-w-[420px]">
        <div className="flex justify-center mb-8">
          <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-[#3b82f6] text-white font-bold text-lg tracking-tight">
            DS
          </div>
        </div>
        <div className="bg-[var(--bg-card,#121212)] border border-[var(--border,#27272a)] rounded-2xl p-8 shadow-2xl">
          <div className="text-center mb-6">
            <h1 className="text-2xl font-semibold tracking-tight text-[var(--text-primary,#ffffff)]">Join your team</h1>
            <p className="text-sm text-[var(--text-secondary,#a1a1aa)] mt-1">
              You were invited to join an organization. Set your name and password to activate your login.
            </p>
          </div>
          {error && (
            <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">{error}</div>
          )}
          {done && (
            <div className="mb-4 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm">{done}</div>
          )}
          <form onSubmit={submit} className="space-y-4">
            {!searchParams.get("token") && (
              <div>
                <label htmlFor="invite-token" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                  Invite token
                </label>
                <input
                  id="invite-token"
                  type="text"
                  placeholder="Paste invite token"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  required
                  disabled={isLoading}
                  className="w-full px-3 py-2.5 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
                />
              </div>
            )}
            <div>
              <label htmlFor="fullName" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                Full Name
              </label>
              <input
                id="fullName"
                type="text"
                placeholder="Dr Sharma"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                required
                disabled={isLoading}
                className="w-full px-3 py-2.5 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
              />
            </div>
            <div>
              <label htmlFor="password" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
                Password
              </label>
              <input
                id="password"
                type="password"
                placeholder="Min. 6 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                disabled={isLoading}
                className="w-full px-3 py-2.5 bg-[var(--bg-primary,#0a0a0a)] border border-border rounded-lg text-sm outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
              />
            </div>
            <button
              type="submit"
              disabled={isLoading}
              className="w-full h-11 bg-[#3b82f6] hover:bg-[#2563eb] text-white font-semibold rounded-lg text-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2 mt-6"
            >
              {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
              {isLoading ? "Activating…" : "Activate my login"}
            </button>
          </form>
          <p className="text-center text-sm text-[var(--text-muted,#71717a)] mt-6">
            Already have an account?{" "}
            <Link href="/login" className="text-[#3b82f6] hover:underline font-medium">
              Sign in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

export default function AcceptInvitePage() {
  return (
    <Suspense fallback={<div className="min-h-screen flex items-center justify-center text-sm text-muted-foreground">Loading…</div>}>
      <AcceptInviteForm />
    </Suspense>
  );
}
