"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { AppSidebar } from "@/components/app-sidebar";
import { Toaster } from "sonner";
import { ImpersonationBanner } from "@/components/impersonation-banner";
import { AnnouncementBell } from "@/components/announcement-bell";
import { CommandPalette } from "@/components/command-palette";
import { LoaderPulseSquare } from "@/components/ui/loaders";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { Rocket } from "lucide-react";

/** Read the sidebar_state cookie that sidebar.tsx writes on toggle. */
function getPersistedSidebarState(): boolean {
  if (typeof document === "undefined") return true;
  const match = document.cookie.match(/(?:^|;\s*)sidebar_state=([^;]*)/);
  if (!match) return true; // default open on first visit
  return match[1] !== "false";
}

function getBreadcrumb(pathname: string) {
  if (pathname === "/dashboard") return { category: "App", page: "Dashboard" };
  if (pathname.includes("/conversations")) return { category: "App", page: "Inbox" };
  if (pathname.includes("/bookings/cancelled")) return { category: "App", page: "Cancelled Bookings" };
  if (pathname.includes("/bookings")) return { category: "App", page: "Bookings" };
  if (pathname.includes("/calendar")) return { category: "App", page: "Calendar" };
  if (pathname.includes("/agent")) return { category: "Settings", page: "Agent Personalisation" };
  if (pathname.includes("/business-hours")) return { category: "Settings", page: "Business Hours" };
  if (pathname.includes("/integrations")) return { category: "Settings", page: "Integration" };
  if (pathname.includes("/support")) return { category: "Settings", page: "Support" };
  if (pathname.includes("/billing")) return { category: "App", page: "Billing" };
  if (pathname.includes("/settings")) return { category: "", page: "Settings" };
  return { category: "App", page: "Dashboard" };
}

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const breadcrumb = getBreadcrumb(pathname);
  // Read persisted sidebar state from cookie so collapse survives navigation
  const [sidebarOpen] = useState<boolean>(getPersistedSidebarState);
  const [onboardingComplete, setOnboardingComplete] = useState<boolean | null>(null);

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace("/login");
    }
  }, [isLoading, user, router]);

  useEffect(() => {
    if (!isLoading && user) {
      let cancelled = false;
      fetch("/api/v1/dashboard/onboarding/status")
        .then((res) => res.json())
        .then((data) => {
          if (!cancelled) setOnboardingComplete(Boolean(data.onboarding_completed));
        })
        .catch(() => {
          // On failure, don't lock the user out — treat as complete.
          if (!cancelled) setOnboardingComplete(true);
        });
      return () => {
        cancelled = true;
      };
    }
  }, [isLoading, user]);

  if (isLoading || (user && onboardingComplete === null)) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <LoaderPulseSquare className="size-16 text-primary" />
      </div>
    );
  }

  if (!user) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <LoaderPulseSquare className="size-16 text-primary" />
      </div>
    );
  }

  // ── Mandatory onboarding gate ────────────────────────────────────────
  // Until onboarding is complete the dashboard renders blurred and
  // non-interactive, with a card linking to the onboarding wizard.
  if (!onboardingComplete) {
    return (
      <div className="relative h-screen overflow-hidden bg-background">
        {/* Blurred, inert preview of the real dashboard shell */}
        <div aria-hidden className="pointer-events-none select-none blur-sm opacity-50">
          <div className="flex h-16 items-center gap-3 border-b border-border px-8">
            <span className="text-sm font-medium text-muted-foreground">
              {breadcrumb.category ? `${breadcrumb.category} / ` : ""}
              <span className="text-foreground">{breadcrumb.page}</span>
            </span>
          </div>
          <main className="flex flex-col gap-6 px-[60px] py-10">
            <div className="h-10 w-72 rounded-lg bg-secondary" />
            <div className="grid grid-cols-4 gap-4">
              {[...Array(4)].map((_, i) => (
                <div key={i} className="h-[118px] rounded-2xl border border-border bg-card" />
              ))}
            </div>
            <div className="grid grid-cols-3 gap-4">
              <div className="h-64 rounded-2xl border border-border bg-card" />
              <div className="h-64 rounded-2xl border border-border bg-card" />
              <div className="h-64 rounded-2xl border border-border bg-card" />
            </div>
          </main>
        </div>

        {/* Gate card */}
        <div className="absolute inset-0 z-10 flex items-center justify-center p-4">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-8 text-center shadow-2xl">
            <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <Rocket className="size-6" />
            </div>
            <h1 className="text-xl font-semibold tracking-tight">
              Finish setup to unlock your dashboard
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              We need a few mandatory details — your industry, timezone and
              business hours — before your booking assistant can go live.
            </p>
            <button
              onClick={() => router.replace("/onboarding")}
              className="mt-6 inline-flex h-10 w-full items-center justify-center rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 cursor-pointer"
            >
              Complete setup now
            </button>
            <button
              onClick={() => {
                // Allow re-check (e.g. completed in another tab)
                setOnboardingComplete(null);
              }}
              className="mt-3 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            >
              I already completed it — recheck
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <SidebarProvider defaultOpen={sidebarOpen}>
      <AppSidebar />
      <ImpersonationBanner />
      <Toaster position="bottom-right" theme="system" />
      <SidebarInset className="bg-background">
        {/* Top Bar — matches template: h-64px, border-bottom, breadcrumbs left, status+button right */}
        <header className="flex h-16 shrink-0 items-center justify-between border-b border-border px-4 sm:px-6 md:px-8 bg-background sticky top-0 z-30">
          <div className="flex items-center gap-2 min-w-0">
            <SidebarTrigger className="-ml-1 text-muted-foreground" />
            <Separator orientation="vertical" className="mr-2 h-4 hidden sm:block" />
            <span className="text-sm font-medium text-muted-foreground truncate">
              <span className="hidden sm:inline">{breadcrumb.category ? `${breadcrumb.category} / ` : ""}</span>
              <span className="text-foreground">{breadcrumb.page}</span>
            </span>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <CommandPalette />
            <span className="hidden sm:inline text-[11px] font-semibold uppercase px-2 py-1 rounded bg-primary/10 text-primary">
              Agent Online
            </span>
            <AnnouncementBell />
          </div>
        </header>
        {/* Main content — matches template: padding 40px 60px, gap 32px */}
        <main className="flex flex-1 flex-col gap-6 md:gap-8 px-4 sm:px-6 md:px-8 lg:px-[60px] py-6 md:py-10 overflow-x-hidden">
          {children}
        </main>
      </SidebarInset>
    </SidebarProvider>
  );
}
