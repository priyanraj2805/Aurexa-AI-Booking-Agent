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
  Menu,
  X,
  ScrollText,
  Settings,
  Users,
  Sun,
  Moon,
  Palette,
  Sparkles,
} from "lucide-react";
import { AuthProvider, useAuth } from "@/lib/auth-context";
import { ToastProvider } from "@/components/toast";
import { ImpersonationBanner } from "@/components/impersonation-banner";
import { NotificationBell } from "@/components/notification-bell";
import { api } from "@/lib/api";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuLabel,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
  DropdownSubItem,
} from "@/components/dropdown-menu";

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
  { name: "Broadcasts", path: "/dashboard/broadcasts", permissions: ["notifications.view"], icon: Megaphone },
  { name: "Support", path: "/dashboard/support", permissions: ["support.tickets.view", "support.impersonate", "support.conversations.view"], icon: LifeBuoy },
  { name: "Settings", path: "/dashboard/settings", permissions: ["security.manage"], icon: Settings },
];

const SIDEBAR_STATE_KEY = "super_admin_sidebar_collapsed";
const THEME_KEY = "super_admin_theme";

function UserMenu({ collapsed }: { collapsed: boolean }) {
  const { admin, logout } = useAuth();
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(THEME_KEY);
      if (stored === "light") setTheme("light");
    } catch {}
    setMounted(true);
  }, []);

  const switchTheme = (t: "dark" | "light") => {
    setTheme(t);
    if (t === "light") {
      document.documentElement.setAttribute("data-theme", "light");
    } else {
      document.documentElement.removeAttribute("data-theme");
    }
    try { localStorage.setItem(THEME_KEY, t); } catch {}
  };

  if (!admin) return null;

  if (collapsed) {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger
          className="flex items-center justify-center rounded-full bg-indigo-600/20 text-indigo-400 hover:bg-indigo-600/30 transition-colors"
          title={admin.full_name || admin.email}
        >
          <div className="h-8 w-8 rounded-full bg-indigo-600/20 flex items-center justify-center text-sm font-medium">
            {(admin.full_name || admin.email || "?").charAt(0).toUpperCase()}
          </div>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="w-56">
          <DropdownMenuLabel>Signed in as</DropdownMenuLabel>
          <div className="px-2 pb-1 text-sm text-slate-200 truncate">{admin.email}</div>
          <DropdownMenuSeparator />
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>
              <Palette className="size-4" /> Theme
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownSubItem onClick={() => switchTheme("light")} active={mounted && theme === "light"}>
                <Sun className="size-3.5" /> White theme
              </DropdownSubItem>
              <DropdownSubItem onClick={() => switchTheme("dark")} active={mounted && theme === "dark"}>
                <Moon className="size-3.5" /> Dark theme
              </DropdownSubItem>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuSeparator />
          <DropdownMenuItem destructive onClick={() => logout()}>
            <LogOut className="size-4" />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    );
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left transition-colors hover:bg-slate-800">
        <div className="h-8 w-8 shrink-0 rounded-full bg-indigo-600/20 flex items-center justify-center text-sm font-medium text-indigo-400">
          {(admin.full_name || admin.email || "?").charAt(0).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-200 truncate">{admin.full_name || admin.email}</p>
          <p className="text-xs text-slate-400 truncate">{admin.role_name}</p>
        </div>
        <svg className="size-4 shrink-0 text-slate-500" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 2l4 4-4 4"/></svg>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-56">
        <DropdownMenuLabel>Signed in as</DropdownMenuLabel>
        <div className="px-2 pb-1 text-sm text-slate-200 truncate">{admin.email}</div>
        <DropdownMenuSeparator />
        <DropdownMenuSub>
          <DropdownMenuSubTrigger>
            <Palette className="size-4" /> Theme
          </DropdownMenuSubTrigger>
          <DropdownMenuSubContent>
            <DropdownSubItem onClick={() => switchTheme("light")} active={mounted && theme === "light"}>
              <Sun className="size-3.5" /> White theme
            </DropdownSubItem>
            <DropdownSubItem onClick={() => switchTheme("dark")} active={mounted && theme === "dark"}>
              <Moon className="size-3.5" /> Dark theme
            </DropdownSubItem>
          </DropdownMenuSubContent>
        </DropdownMenuSub>
        <DropdownMenuSeparator />
        <DropdownMenuItem destructive onClick={() => logout()}>
          <LogOut className="size-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const { admin, isLoading, can } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [counts, setCounts] = useState<Record<string, number>>({});

  const POLL_MS = 30000;

  useEffect(() => {
    if (!isLoading && !admin) {
      router.replace("/login");
    }
  }, [isLoading, admin, router]);

  useEffect(() => {
    if (isLoading || !admin) return;
    let alive = true;
    const load = async () => {
      const next: Record<string, number> = {};
      const jobs: { key: string; run: () => Promise<number> }[] = [];
      if (can("orgs.delete")) {
        jobs.push({
          key: "/dashboard/approvals",
          run: async () => {
            const r = await api.get<{ items: unknown[] }>(
              "/approvals?status=PENDING&limit=200"
            );
            return r.items.length;
          },
        });
      }
      if (can("health.view")) {
        jobs.push({
          key: "/dashboard/incidents",
          run: async () => {
            const r = await api.get<{ total: number }>(
              "/incidents?status=open&page_size=1"
            );
            return r.total;
          },
        });
      }
      if (can("support.tickets.view")) {
        jobs.push({
          key: "/dashboard/support",
          run: async () => {
            const r = await api.get<{ items: unknown[] }>(
              "/support/queue?status=open&limit=100"
            );
            return r.items.length;
          },
        });
      }
      await Promise.all(
        jobs.map(async (j) => {
          try {
            next[j.key] = await j.run();
          } catch (err) {
            console.error(`[nav-badge] failed to load count for ${j.key}`, err);
          }
        })
      );
      if (alive) setCounts(next);
    };
    void load();
    const t = setInterval(load, POLL_MS);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      alive = false;
      clearInterval(t);
      window.removeEventListener("focus", onFocus);
    };
  }, [isLoading, admin, can]);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(SIDEBAR_STATE_KEY) === "1");
    } catch {}
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem(SIDEBAR_STATE_KEY, next ? "1" : "0");
      } catch {}
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
  const navCollapsed = collapsed && !mobileNavOpen;

  return (
    <div className="flex h-dvh overflow-hidden">
      {mobileNavOpen && (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-40 bg-black/60 md:hidden"
          onClick={() => setMobileNavOpen(false)}
        />
      )}
      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-60 shrink-0 flex-col border-r border-slate-800 bg-slate-900 pt-16 transition-transform duration-200 md:relative md:z-auto md:translate-x-0 md:transition-[width] ${
          collapsed ? "md:w-16" : "md:w-60"
        } ${mobileNavOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0"}`}
      >
        <div
          className={`flex items-center pb-4 ${
            navCollapsed ? "justify-center px-2" : "justify-between px-4"
          }`}
        >
          {!navCollapsed && (
            <div className="flex items-center gap-2.5 min-w-0">
              <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 shadow-md shadow-indigo-600/30">
                <Sparkles className="size-4 text-white" />
              </span>
              <div className="min-w-0 leading-tight">
                <p className="truncate text-sm font-bold text-slate-100">DotStark</p>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
                  Super Admin
                </p>
              </div>
            </div>
          )}
          {navCollapsed && (
            <span className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 shadow-md shadow-indigo-600/30">
              <Sparkles className="size-4 text-white" />
            </span>
          )}
          <button
            onClick={toggleCollapsed}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="hidden rounded-md p-2 text-slate-400 hover:bg-slate-800 hover:text-slate-200 md:block"
          >
            {collapsed ? (
              <PanelLeftOpen className="h-4 w-4" />
            ) : (
              <PanelLeftClose className="h-4 w-4" />
            )}
          </button>
        </div>
        <nav className="space-y-1 overflow-y-auto px-4 flex-1 min-h-0">
          {visibleModules.map((m) => {
            const active =
              m.path === "/dashboard"
                ? pathname === "/dashboard"
                : pathname === m.path || pathname.startsWith(m.path + "/");
            const Icon = m.icon;
            const badge = counts[m.path] ?? 0;
            return (
              <Link
                key={m.path}
                href={m.path}
                onClick={() => setMobileNavOpen(false)}
                title={navCollapsed ? m.name : undefined}
                className={`relative flex min-h-11 items-center rounded-lg text-sm transition-all duration-200 ${
                  navCollapsed ? "justify-center px-0 py-2" : "gap-3 px-3 py-2"
                } ${
                  active
                    ? "bg-gradient-to-r from-indigo-600 to-indigo-500 font-medium text-white shadow-md shadow-indigo-600/25"
                    : "text-slate-300 hover:bg-slate-800 hover:text-slate-100"
                }`}
              >
                <Icon
                  className={`shrink-0 ${navCollapsed ? "h-5 w-5" : "h-4 w-4"}`}
                />
                {!navCollapsed && <span className="truncate">{m.name}</span>}
                {badge > 0 && (
                  <>
                    {!navCollapsed && (
                      <span className="ml-auto flex h-5 min-w-5 items-center justify-center rounded-full bg-red-600 px-1.5 text-xs font-bold text-white">
                        {badge > 99 ? "99+" : badge}
                      </span>
                    )}
                    {navCollapsed && (
                      <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-red-500" />
                    )}
                  </>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="mt-6 border-t border-slate-800 pt-4 px-4 shrink-0">
          <UserMenu collapsed={navCollapsed} />
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <ImpersonationBanner />
        <div className="sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/80 px-4 py-2 backdrop-blur sm:px-6">
          <button
            type="button"
            aria-label={mobileNavOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={mobileNavOpen}
            onClick={() => setMobileNavOpen((open) => !open)}
            className="rounded-md p-2 text-slate-300 hover:bg-slate-800 md:hidden"
          >
            {mobileNavOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
          <NotificationBell />
        </div>
        <div className="min-w-0 p-4 sm:p-6 lg:p-8">{children}</div>
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
