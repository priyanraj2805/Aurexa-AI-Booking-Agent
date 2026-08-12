"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Building2,
  CheckCheck,
  CreditCard,
  Gem,
  LayoutDashboard,
  LifeBuoy,
  LogOut,
  Megaphone,
  PanelLeftClose,
  PanelLeftOpen,
  ScrollText,
  Settings,
  Users,
} from "lucide-react";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { ToastProvider } from "@/components/toast";
import { ImpersonationBanner } from "@/components/impersonation-banner";
import { NotificationBell } from "@/components/notification-bell";

type NavModule = {
  name: string;
  path: string;
  permissions: string[];
  icon: React.ComponentType<{ className?: string }>;
};

const MODULES: NavModule[] = [
  { name: "Dashboard", path: "/dashboard", permissions: ["dashboard.view"], icon: LayoutDashboard },
  { name: "Organizations", path: "/dashboard/organizations", permissions: ["orgs.view"], icon: Building2 },
  { name: "Users", path: "/dashboard/users", permissions: ["users.view"], icon: Users },
  { name: "Plans", path: "/dashboard/plans", permissions: ["plans.view"], icon: Gem },
  { name: "Billing", path: "/dashboard/billing", permissions: ["billing.view"], icon: CreditCard },
  { name: "Analytics", path: "/dashboard/reports", permissions: ["reports.view"], icon: BarChart3 },
  { name: "Monitoring", path: "/dashboard/monitoring", permissions: ["health.view"], icon: Activity },
  { name: "Incidents", path: "/dashboard/incidents", permissions: ["incidents.view"], icon: AlertTriangle },
  { name: "Audit Logs", path: "/dashboard/audit", permissions: ["audit.view"], icon: ScrollText },
  { name: "Approvals", path: "/dashboard/approvals", permissions: ["orgs.delete"], icon: CheckCheck },
  { name: "Notifications", path: "/dashboard/notifications", permissions: ["notifications.view"], icon: Megaphone },
  { name: "Support", path: "/dashboard/support", permissions: ["support.tickets.view", "support.impersonate", "support.conversations.view"], icon: LifeBuoy },
  { name: "Settings", path: "/dashboard/settings", permissions: ["security.manage"], icon: Settings },
];

const SIDEBAR_STATE_KEY = "super_admin_sidebar_collapsed";

function Shell({ children }: { children: React.ReactNode }) {
  const { admin, isLoading, can, logout } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    if (!isLoading && !admin) {
      router.replace("/login");
    }
  }, [isLoading, admin, router]);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(SIDEBAR_STATE_KEY) === "1");
    } catch {
      // storage unavailable — keep default expanded
    }
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_STATE_KEY, next ? "1" : "0");
      } catch {
        // storage unavailable — state still works for the session
      }
      return next;
    });
  };

  if (isLoading || !admin) {
    return (
      <div className="flex min-h-screen items-center justify-center text-slate-400">
        Checking session…
      </div>
    );
  }

  const visibleModules = MODULES.filter((m) =>
    m.permissions.some((p) => can(p))
  );

  return (
    <div className="flex h-screen overflow-hidden">
      <ImpersonationBanner />
      <aside
        className={`shrink-0 overflow-y-auto border-r border-slate-800 bg-slate-900 p-4 pt-16 transition-[width] duration-200 ${
          collapsed ? "w-16" : "w-60"
        }`}
      >
        <div
          className={`flex items-center pb-4 ${
            collapsed ? "justify-center" : "justify-between px-2"
          }`}
        >
          {!collapsed && (
            <span className="text-sm font-bold uppercase tracking-wide text-slate-400">
              Super Admin
            </span>
          )}
          <button
            onClick={toggleCollapsed}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            {collapsed ? (
              <PanelLeftOpen className="h-4 w-4" />
            ) : (
              <PanelLeftClose className="h-4 w-4" />
            )}
          </button>
        </div>
        <nav className="space-y-1">
          {visibleModules.map((m) => {
            const active =
              m.path === "/dashboard"
                ? pathname === "/dashboard"
                : pathname === m.path || pathname.startsWith(m.path + "/");
            const Icon = m.icon;
            return (
              <Link
                key={m.path}
                href={m.path}
                title={collapsed ? m.name : undefined}
                className={`flex items-center rounded-md text-sm ${
                  collapsed ? "justify-center px-0 py-2" : "gap-3 px-3 py-2"
                } ${
                  active
                    ? "bg-indigo-600 font-medium text-white"
                    : "text-slate-300 hover:bg-slate-800"
                }`}
              >
                <Icon
                  className={`shrink-0 ${collapsed ? "h-5 w-5" : "h-4 w-4"}`}
                />
                {!collapsed && <span className="truncate">{m.name}</span>}
              </Link>
            );
          })}
        </nav>
        <div className="mt-6 border-t border-slate-800 pt-4">
          {collapsed ? (
            <div className="flex justify-center">
              <button
                onClick={logout}
                title="Sign out"
                aria-label="Sign out"
                className="rounded-md p-1.5 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
              >
                <LogOut className="h-5 w-5" />
              </button>
            </div>
          ) : (
            <>
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
            </>
          )}
        </div>
      </aside>
      <main className="flex-1 overflow-y-auto">
        <div className="sticky top-0 z-30 flex items-center justify-end border-b border-slate-800 bg-slate-950/80 px-6 py-2 backdrop-blur">
          <NotificationBell />
        </div>
        <div className="p-8">{children}</div>
      </main>
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