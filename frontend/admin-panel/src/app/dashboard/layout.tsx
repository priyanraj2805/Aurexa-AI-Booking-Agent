"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { AppSidebar } from "@/components/app-sidebar";
import { ThemeToggle } from "@/components/theme-toggle";
import { Toaster } from "sonner";
import { ImpersonationBanner } from "@/components/impersonation-banner";
import { AnnouncementBell } from "@/components/announcement-bell";
import { LoaderPulseSquare } from "@/components/ui/loaders";
import {
  SidebarInset,
  SidebarProvider,
  SidebarTrigger,
} from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";

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
  if (pathname.includes("/calendar")) return { category: "App", page: "Calendar" };
  if (pathname.includes("/agent")) return { category: "Settings", page: "Agent Personalisation" };
  if (pathname.includes("/business-hours")) return { category: "Settings", page: "Business Hours" };
  if (pathname.includes("/integrations")) return { category: "Settings", page: "Integration" };
  if (pathname.includes("/support")) return { category: "Settings", page: "Support" };
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
  const [widgetToken, setWidgetToken] = useState<string>("");
  // Read persisted sidebar state from cookie so collapse survives navigation
  const [sidebarOpen] = useState<boolean>(getPersistedSidebarState);

  useEffect(() => {
    async function fetchWidgetToken() {
      try {
        const data = await api.get<{ widget_token?: string }>("/dashboard/widget-token");
        setWidgetToken(data?.widget_token || "");
      } catch (e) {
        console.error("Failed to fetch widget token for preview:", e);
      }
    }
    fetchWidgetToken();
  }, []);

  const handlePreviewAI = () => {
    const tokenForPreview = widgetToken && widgetToken.length > 12 ? widgetToken : "ds_wid_demo_token";
    const WIDGET_DEV_URL = "http://localhost:5174";
    const envApiBase = process.env.NEXT_PUBLIC_API_BASE || "/api/v1";
    const API_BASE_URL = /^https?:\/\//i.test(envApiBase)
      ? envApiBase
      : `${window.location.origin}${envApiBase.startsWith("/") ? envApiBase : `/${envApiBase}`}`;
    const previewHtml = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AI Booking Agent Preview</title>
  <style>
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { 
      min-height: 100vh; 
      background: linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%); 
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    .preview-container {
      padding: 20px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
    }
    .preview-msg {
      color: #666;
      font-size: 14px;
      text-align: center;
      margin-bottom: 20px;
    }
    .preview-header {
      background: white;
      padding: 15px 25px;
      border-radius: 12px;
      box-shadow: 0 2px 10px rgba(0,0,0,0.1);
      margin-bottom: 20px;
    }
    .preview-header h2 {
      font-size: 18px;
      color: #333;
      margin-bottom: 5px;
    }
    .preview-header p {
      font-size: 12px;
      color: #888;
    }
  </style>
</head>
<body>
  <div class="preview-container">
    <div class="preview-header">
      <h2>🤖 AI Booking Agent</h2>
      <p>Preview Mode - Say "Hi" to start</p>
    </div>
    <p class="preview-msg">Your booking widget should appear in the bottom-right corner.</p>
  </div>
  <script src="${WIDGET_DEV_URL}/src/index.js"
    data-widget-token="${tokenForPreview}"
    data-api-base="${API_BASE_URL}"
    data-color="#3B82F6"
    defer>
  </script>
</body>
</html>`;
    const blob = new Blob([previewHtml], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "width=450,height=700,scrollbars=yes");
  };

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace("/login");
    }
  }, [isLoading, user, router]);

  if (isLoading) {
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
              <span className="hidden sm:inline">{breadcrumb.category} / </span>
              <span className="text-foreground">{breadcrumb.page}</span>
            </span>
          </div>
          <div className="flex items-center gap-2 sm:gap-3">
            <span className="hidden sm:inline text-[11px] font-semibold uppercase px-2 py-1 rounded bg-primary/10 text-primary">
              Agent Online
            </span>
            <AnnouncementBell />
            <ThemeToggle />
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
