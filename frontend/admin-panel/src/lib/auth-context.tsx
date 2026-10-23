"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { apiFetch } from "@/lib/api";
import { useRouter } from "next/navigation";

// ── Types ────────────────────────────────────────

interface User {
  id: string;
  email: string;
  full_name: string;
  org_id: string;
  org_name: string | null;
  org_type?: string;
  avatar_url: string | null;
  is_active: boolean;
  created_at: string;
  roles: string[];
  permissions: string[];
}

export interface StaffScope {
  org_type: string;
  roles: string[];
  provider_id: string | null;
  provider_name?: string | null;
  unit_ids: string[];
  unit_names?: string[];
  provider_unit_names?: string[];
}

interface AuthContextType {
  user: User | null;
  scope: StaffScope | null;
  /** True for a linked doctor login — sees only own patients, no org management. */
  isDoctor: boolean;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  sendOtp: (email: string) => Promise<void>;
  register: (data: RegisterData) => Promise<void>;
  requestPasswordReset: (email: string) => Promise<void>;
  resetPassword: (email: string, code: string, newPassword: string) => Promise<void>;
  updateProfile: (fullName: string) => Promise<void>;
  refresh: () => Promise<void>;
  setUser: (u: User | null) => void;
  logout: () => void;
}

interface RegisterData {
  full_name: string;
  org_name: string;
  email: string;
  password: string;
  otp_code: string;
  org_type?: "individual" | "organization";
}

interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

// Cross-tab session sync: one browser = one HttpOnly cookie. If the user
// logs in/out in another tab, that tab overwrites the shared cookie and
// this tab would keep showing the OLD dashboard until manual refresh.
// Broadcasting a localStorage ping lets every open tab re-validate itself.
const AUTH_SYNC_KEY = "ds_auth_changed";

export function notifyAuthChange() {
  try {
    localStorage.setItem(AUTH_SYNC_KEY, String(Date.now()));
  } catch {
    // private mode etc — single-tab case still works via cookie
  }
}

// ── Context ──────────────────────────────────────

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [scope, setScope] = useState<StaffScope | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  // On mount: validate auth with /auth/me (uses HttpOnly cookie)
  // Use raw fetch to avoid the 401→redirect in apiFetch
  useEffect(() => {
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";
    fetch(`${API_BASE}/auth/me`, {
      credentials: "include",
      headers: { "X-Requested-With": "XMLHttpRequest" },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`Auth check failed: ${res.status}`);
        return res.json();
      })
      .then((freshUser) => {
        setUser(freshUser);
        // Best-effort staff scope (doctor link) for menu gating.
        fetch(`${API_BASE}/staff/me/scope`, {
          credentials: "include",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        })
          .then((r) => (r.ok ? r.json() : null))
          .then((s) => setScope(s))
          .catch(() => setScope(null));
      })
      .catch(() => {
        setUser(null);
        setScope(null);
      })
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    const handleUnauthorized = () => {
      setUser(null);
      if (window.location.pathname !== "/login" && !window.location.pathname.startsWith("/register")) {
        router.push("/login");
      }
    };
    window.addEventListener("auth:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", handleUnauthorized);
  }, [router]);

  // If another tab changed the shared session cookie (login/logout there),
  // silently re-validate so this tab never shows a stale account's dashboard.
  // Focus check is throttled: at most once every 30s per tab.
  useEffect(() => {
    let lastCheck = 0;
    const revalidate = async (force = false) => {
      const now = Date.now();
      if (!force && now - lastCheck < 30000) return;
      lastCheck = now;
      const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";
      try {
        const res = await fetch(`${API_BASE}/auth/me`, {
          credentials: "include",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        if (!res.ok) throw new Error(`Auth check failed: ${res.status}`);
        setUser(await res.json());
      } catch {
        // Keep the current user on transient failures — only the 401
        // interceptor performs redirects.
      }
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === AUTH_SYNC_KEY) void revalidate(true);
    };
    const onFocus = () => {
      void revalidate(false);
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  const login = async (email: string, password: string) => {
    // URL-handoff code flow: credentials → single-use code (visible in URL)
    // → callback exchanges it for the HttpOnly cookie. Old direct login
    // still works server-side; this client prefers the code flow.
    try {
      const data = await apiFetch<{ code: string }>("/auth/login/code", {
        method: "POST",
        body: JSON.stringify({ email, password }),
        skipAuth: true,
      });
      // Full navigation so the code visibly lands in the URL bar.
      // Replace the current (login) history entry with the landing page
      // first, so pressing the browser Back button from the dashboard
      // lands on the homepage instead of the sign-in page.
      try {
        window.history.replaceState({}, "", "/");
      } catch {
        // ignore — best-effort history hygiene
      }
      window.location.href = `/auth/callback?code=${encodeURIComponent(data.code)}`;
    } catch (err) {
      throw err;
    }
  };

  const sendOtp = async (email: string) => {
    // Register-only: login is a plain email + password flow with no OTP.
    await apiFetch<{ detail: string }>("/auth/otp/send", {
      method: "POST",
      body: JSON.stringify({ email, purpose: "register" }),
      skipAuth: true,
    });
  };

  const register = async (regData: RegisterData) => {
    await apiFetch<TokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(regData),
      skipAuth: true,
    });

    // No auto-login: the calling page decides where the user goes next
    // (landing page slides to the Sign In tab, standalone /register redirects).
  };

  const requestPasswordReset = async (email: string) => {
    // Generic response either way — the backend never reveals whether the
    // email is registered.
    await apiFetch<{ detail: string }>("/auth/password/forgot", {
      method: "POST",
      body: JSON.stringify({ email }),
      skipAuth: true,
    });
  };

  const resetPassword = async (email: string, code: string, newPassword: string) => {
    await apiFetch<{ detail: string }>("/auth/password/reset", {
      method: "POST",
      body: JSON.stringify({ email, otp_code: code, new_password: newPassword }),
      skipAuth: true,
    });
  };

  const updateProfile = async (fullName: string) => {
    const data = await apiFetch<{ ok: boolean; full_name: string }>("/account/profile", {
      method: "PATCH",
      body: JSON.stringify({ full_name: fullName }),
    });
    setUser((prev) => (prev ? { ...prev, full_name: data.full_name } : prev));
  };

  const refresh = async () => {
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";
    try {
      const res = await fetch(`${API_BASE}/auth/me`, {
        credentials: "include",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      if (!res.ok) throw new Error("refresh failed");
      const fresh = await res.json();
      setUser(fresh);
      try {
        const scopeRes = await fetch(`${API_BASE}/staff/me/scope`, {
          credentials: "include",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        setScope(scopeRes.ok ? await scopeRes.json() : null);
      } catch {
        // scope is best-effort
      }
    } catch {
      // ignore
    }
  };

  const logout = async () => {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch {
      // Best-effort
    }
    setUser(null);
    setScope(null);
    notifyAuthChange();
    router.push("/login");
  };

  const setUserWrapped = (u: User | null) => {
    setUser(u);
    if (!u) setScope(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        scope,
        isDoctor: !!scope?.provider_id,
        isLoading,
        isAuthenticated: !!user,
        login,
        sendOtp,
        register,
        requestPasswordReset,
        resetPassword,
        updateProfile,
        refresh,
        setUser: setUserWrapped,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
