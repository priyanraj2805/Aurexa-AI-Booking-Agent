"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { ToastProvider } from "@/components/toast";

const MODULES = [
  { name: "Dashboard", path: "/dashboard", permission: "dashboard.view" },
  { name: "Organizations", path: "/dashboard/organizations", permission: "orgs.view" },
  { name: "Users", path: "/dashboard/users", permission: "users.view" },
  { name: "Plans", path: "/dashboard/plans", permission: "plans.view" },
  { name: "Billing", path: "/dashboard/billing", permission: "billing.view" },
  { name: "Analytics", path: "/dashboard/reports", permission: "reports.view" },
  { name: "Monitoring", path: "/dashboard/monitoring", permission: "health.view" },
  { name: "Alerts", path: "/dashboard/alerts", permission: "alerts.view" },
  { name: "Incidents", path: "/dashboard/incidents", permission: "incidents.view" },
  { name: "Feature Flags", path: "/dashboard/flags", permission: "ops.manage" },
  { name: "Audit Logs", path: "/dashboard/audit", permission: "audit.view" },
  { name: "Approvals", path: "/dashboard/approvals", permission: "orgs.delete" },
  { name: "Notifications", path: "/dashboard/notifications", permission: "ops.manage" },
  { name: "Support", path: "/dashboard/support", permission: "support.impersonate" },
  { name: "Settings", path: "/dashboard/settings", permission: "ops.manage" },
];

function Shell({ children }: { children: React.ReactNode }) {
  const { admin, isLoading, can, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!isLoading && !admin) {
      router.replace("/login");
    }
  }, [isLoading, admin, router]);

  if (isLoading || !admin) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-400">
        Checking session…
      </div>
    );
  }

  const visibleModules = MODULES.filter((m) => can(m.permission));

  return (
    <div className="flex min-h-screen">
      <aside className="w-60 shrink-0 border-r border-slate-800 bg-slate-900 p-4">
        <div className="px-2 pb-4 text-sm font-bold uppercase tracking-wide text-slate-400">
          Super Admin
        </div>
        <nav className="space-y-1">
          {visibleModules.map((m) => {
            const active = pathname.startsWith(m.path);
            return (
              <Link
                key={m.path}
                href={m.path}
                className={`block rounded-md px-3 py-2 text-sm ${
                  active
                    ? "bg-indigo-600 font-medium text-white"
                    : "text-slate-300 hover:bg-slate-800"
                }`}
              >
                {m.name}
              </Link>
            );
          })}
        </nav>
        <div className="mt-6 border-t border-slate-800 pt-4">
          <p className="px-2 text-sm font-medium text-slate-200">
            {admin.full_name || admin.email}
          </p>
          <p className="px-2 text-xs text-slate-400">{admin.role_name}</p>
          <button
            onClick={logout}
            className="mt-3 w-full rounded-md border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 p-8">{children}</main>
    </div>
  );
}

export default function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <AuthProvider>
      <ToastProvider>
        <Shell>{children}</Shell>
      </ToastProvider>
    </AuthProvider>
  );
}
