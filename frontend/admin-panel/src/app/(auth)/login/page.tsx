"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import Link from "next/link";
import { Loader2, Eye, EyeOff } from "lucide-react";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [registered, setRegistered] = useState(false);
  const { login, user, isLoading: authLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("registered") === "1") {
      setRegistered(true);
      window.history.replaceState({}, "", "/login");
    }
  }, []);

  // A signed-in visitor has no business on the sign-in form. This is what
  // makes browser Back work from the dashboard: the login history entry sits
  // behind it, so rather than re-showing the form we forward to the homepage,
  // where they can start again or re-enter the dashboard.
  useEffect(() => {
    if (!authLoading && user) router.replace("/");
  }, [authLoading, user, router]);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setIsLoading(true);

    try {
      // Trim accidental whitespace (mobile autocorrect loves trailing spaces).
      await login(email.trim(), password.trim());
    } catch (err: unknown) {
      let message: string;
      if (err instanceof TypeError && (err.message === "Failed to fetch" || err.message === "NetworkError when attempting to fetch resource.")) {
        message = "Cannot connect to server. Please check that the backend is running.";
      } else {
        message = err instanceof Error ? err.message : "Login failed";
      }
      setError(message);
    } finally {
      setIsLoading(false);
    }
  };

  // While the session check runs (or a redirect is in flight) show a spinner,
  // never the sign-in form - otherwise a signed-in visitor pressing Back from
  // the dashboard would still see the form flash first.
  if (authLoading || user) {
    return (
      <div
        role="status"
        aria-label="Checking session"
        className="flex items-center justify-center py-10"
      >
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="w-full max-w-[420px]">
      {/* Logo */}
      <div className="flex justify-center mb-8">
        <div className="flex items-center justify-center w-12 h-12 rounded-xl bg-[#3b82f6] text-white font-bold text-lg tracking-tight">
          DS
        </div>
      </div>

      {/* Card */}
      <div className="bg-[var(--bg-card,#121212)] border border-[var(--border,#27272a)] rounded-2xl p-8 shadow-2xl">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-[var(--text-primary,#ffffff)]">Welcome Back</h1>
          <p className="text-sm text-[var(--text-secondary,#a1a1aa)] mt-1">
            Sign in to manage your AI Booking Assistant
          </p>
        </div>

        {registered && (
          <div className="mb-4 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-400 text-sm">
            Organization created! Sign in with your new credentials to continue.
          </div>
        )}

        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleLogin} className="space-y-4">
          {/* Email */}
          <div>
            <label htmlFor="email" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] mb-1.5 uppercase tracking-wider">
              Email
            </label>
            <input
              id="email"
              type="email"
              placeholder="name@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              disabled={isLoading}
              className="w-full px-3 py-2.5 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] placeholder:text-[var(--text-muted,#71717a)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
            />
          </div>

          {/* Password */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label htmlFor="password" className="block text-xs font-medium text-[var(--text-secondary,#a1a1aa)] uppercase tracking-wider">
                Password
              </label>
              <Link href="/forgot-password" className="text-xs text-[var(--text-muted,#71717a)] hover:text-[#3b82f6] transition-colors">
                Forgot password?
              </Link>
            </div>
            <div className="relative">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                disabled={isLoading}
                className="w-full px-3 py-2.5 pr-10 bg-[var(--bg-primary,#0a0a0a)] border border-[var(--border,#27272a)] rounded-lg text-sm text-[var(--text-primary,#ffffff)] outline-none transition-all focus:border-[#3b82f6] focus:ring-2 focus:ring-[#3b82f6]/20 disabled:opacity-50"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-muted,#71717a)] hover:text-[var(--text-secondary,#a1a1aa)] transition-colors"
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          {/* Submit */}
          <button
            type="submit"
            disabled={isLoading}
            className="w-full h-11 bg-[#3b82f6] hover:bg-[#2563eb] text-white font-semibold rounded-lg text-sm transition-all disabled:opacity-50 flex items-center justify-center gap-2 mt-6"
          >
            {isLoading && <Loader2 className="h-4 w-4 animate-spin" />}
            {isLoading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        <p className="text-center text-sm text-[var(--text-muted,#71717a)] mt-6">
          Don&apos;t have an account?{" "}
          <Link href="/register" className="text-[#3b82f6] hover:underline font-medium">
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}
