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
  is_active: boolean;
  created_at: string;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (data: RegisterData) => Promise<void>;
  logout: () => void;
}

interface RegisterData {
  full_name: string;
  org_name: string;
  email: string;
  password: string;
}

interface TokenResponse {
  access_token: string;
  token_type: string;
  user: User;
}

// ── Context ──────────────────────────────────────

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
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
      .then((freshUser) => setUser(freshUser))
      .catch(() => setUser(null))
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

  const login = async (email: string, password: string) => {
    const data = await apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
      skipAuth: true,
    });

    // HttpOnly cookie is set by the backend response.
    // We only keep user info in memory (not localStorage).
    setUser(data.user);
    router.push("/dashboard");
  };

  const register = async (regData: RegisterData) => {
    const data = await apiFetch<TokenResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(regData),
      skipAuth: true,
    });

    setUser(data.user);
    router.push("/dashboard");
  };

  const logout = async () => {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch {
      // Best-effort
    }
    setUser(null);
    router.push("/login");
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        login,
        register,
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
