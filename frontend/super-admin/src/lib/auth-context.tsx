"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  ReactNode,
} from "react";
import { apiFetch } from "@/lib/api";
import { useRouter } from "next/navigation";

interface Admin {
  id: string;
  email: string;
  full_name: string | null;
  role_code: string;
  role_name: string;
  permissions: string[];
}

interface AuthContextType {
  admin: Admin | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  can: (permission: string) => boolean;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [admin, setAdmin] = useState<Admin | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  // On mount: validate auth with /auth/me (HttpOnly cookie)
  // Raw fetch to avoid the 401→redirect inside apiFetch
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
      .then((freshAdmin) => setAdmin(freshAdmin))
      .catch(() => setAdmin(null))
      .finally(() => setIsLoading(false));
  }, []);

  useEffect(() => {
    const handleUnauthorized = () => {
      setAdmin(null);
      if (
        window.location.pathname !== "/login" &&
        !window.location.pathname.startsWith("/mfa")
      ) {
        router.push("/login");
      }
    };
    window.addEventListener("auth:unauthorized", handleUnauthorized);
    return () =>
      window.removeEventListener("auth:unauthorized", handleUnauthorized);
  }, [router]);

  const logout = async () => {
    try {
      await apiFetch("/auth/logout", { method: "POST" });
    } catch {
      // Best-effort
    }
    setAdmin(null);
    router.push("/login");
  };

  const can = (permission: string) => admin?.permissions.includes(permission) ?? false;

  return (
    <AuthContext.Provider
      value={{
        admin,
        isLoading,
        isAuthenticated: !!admin,
        can,
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
