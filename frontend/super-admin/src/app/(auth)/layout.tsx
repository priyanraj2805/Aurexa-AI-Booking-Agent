"use client";

import { AuthProvider } from "@/lib/auth-context";

export default function AuthLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <AuthProvider>{children}</AuthProvider>;
}
