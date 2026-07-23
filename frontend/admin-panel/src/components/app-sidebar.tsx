"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  Inbox,
  Bot,
  CalendarClock,
  Plug,
  LogOut,
} from "lucide-react";

import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from "@/components/ui/sidebar";
import { useAuth } from "@/lib/auth-context";

const navApp = [
  { title: "Dashboard", url: "/dashboard", icon: LayoutDashboard },
  { title: "Calendar", url: "/dashboard/calendar", icon: CalendarClock },
  { title: "Inbox", url: "/dashboard/conversations", icon: Inbox },
];

const navSettings = [
  { title: "Agent Personalisation", url: "/dashboard/agent", icon: Bot },
  { title: "Business Hours", url: "/dashboard/business-hours", icon: CalendarClock },
  { title: "Integration", url: "/dashboard/integrations", icon: Plug },
];

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();
  const { isMobile, setOpenMobile, openMobile } = useSidebar();

  const isActive = (url: string) => {
    if (url === "/dashboard") return pathname === "/dashboard";
    return pathname === url || pathname.startsWith(url + "/");
  };

  // Close the mobile drawer whenever the route changes (covers clicks, back/forward).
  React.useEffect(() => {
    setOpenMobile(false);
  }, [pathname, setOpenMobile]);

  // Safety net: Base UI's Dialog can ignore a state flip during an active pointer
  // event. Dispatching Escape forces it to close reliably.
  React.useEffect(() => {
    if (isMobile && openMobile) {
      const t = setTimeout(() => {
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      }, 50);
      return () => clearTimeout(t);
    }
  }, [pathname, isMobile, openMobile]);

  const initials = user?.full_name
    ? user.full_name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : "DS";

  // On mobile: stop the default Link navigation, close the drawer, then navigate.
  // Base UI's useRender overwrites onClick passed inside render={<Link/>}, so we
  // pass onClick directly to SidebarMenuButton (merged via mergeProps).
  const handleNavClick = (url: string) => (e: React.MouseEvent) => {
    if (!isMobile) return;
    e.preventDefault();
    e.stopPropagation();
    setOpenMobile(false);
    setTimeout(() => router.push(url), 10);
  };

  return (
    <Sidebar collapsible="icon" {...props}>
      {/* ── Logo ── */}
      <SidebarHeader className="px-4 pt-6 pb-4 transition-[padding] duration-250 ease-in-out overflow-hidden group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center">
        {/* Fix: removed gap-3 in icon mode so the logo doesn't overflow leftwards */}
        <div className="flex items-center gap-3 transition-[gap] duration-250 ease-in-out group-data-[collapsible=icon]:gap-0">
          <div className="flex items-center justify-center size-8 rounded-md bg-foreground text-background font-bold text-sm tracking-tight shrink-0">
            DS
          </div>
          <span
            className={[
              "font-semibold text-base tracking-tight whitespace-nowrap overflow-hidden",
              "transition-[opacity,max-width] duration-250 ease-in-out",
              "group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:max-w-0",
              "max-w-[160px] opacity-100",
            ].join(" ")}
          >
            DotStark AI
          </span>
        </div>
      </SidebarHeader>

      <SidebarContent className="px-2 gap-0 group-data-[collapsible=icon]:px-0">
        {/* ── App Section ── */}
        <div className="mb-1">
          <p
            className={[
              "text-[11px] font-semibold uppercase tracking-wider text-muted-foreground px-2",
              "transition-[opacity,max-height,padding] duration-250 ease-in-out overflow-hidden",
              "max-h-8 opacity-100 py-1.5",
              "group-data-[collapsible=icon]:max-h-0 group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:py-0",
            ].join(" ")}
          >
            App
          </p>
          {/* Fix: removed internal sidebarMenu padding in collapsed state so it perfectly centers */}
          <SidebarMenu className="gap-1 px-1 transition-[padding] duration-250 ease-in-out group-data-[collapsible=icon]:px-0">
            {navApp.map((item) => (
              <SidebarMenuItem key={item.title}>
                <SidebarMenuButton
                  isActive={isActive(item.url)}
                  tooltip={item.title}
                  onClick={handleNavClick(item.url)}
                  className={[
                    "h-10 cursor-pointer rounded-lg text-[13px] transition-all duration-200",
                    "text-muted-foreground hover:text-foreground hover:bg-accent",
                    "data-[active=true]:bg-primary/10 data-[active=true]:text-primary data-[active=true]:font-medium data-[active=true]:shadow-sm data-[active=true]:!ring-0",
                    // Fix: perfectly center icon, force remove underlying gap-2 which pushed icon left
                    "group-data-[collapsible=icon]:w-10 group-data-[collapsible=icon]:h-10 group-data-[collapsible=icon]:!p-0 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:mx-auto group-data-[collapsible=icon]:!gap-0",
                  ].join(" ")}
                  render={<Link href={item.url} prefetch={false} />}
                >
                  <item.icon className="size-5 shrink-0" />
                  <span
                    className={[
                      "transition-[opacity,max-width,margin] duration-200 ease-in-out overflow-hidden whitespace-nowrap",
                      "group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:max-w-0 group-data-[collapsible=icon]:!ml-0",
                      "max-w-[160px] opacity-100 ml-2",
                    ].join(" ")}
                  >
                    {item.title}
                  </span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </div>

        {/* ── Separator (Only visible in icon mode) ── */}
        <div className="w-full h-0 bg-border opacity-0 transition-all duration-250 ease-in-out group-data-[collapsible=icon]:h-px group-data-[collapsible=icon]:my-2 group-data-[collapsible=icon]:opacity-100" />

        {/* ── Settings Section ── */}
        <div className="mt-1">
          <p
            className={[
              "text-[11px] font-semibold uppercase tracking-wider text-muted-foreground px-2",
              "transition-[opacity,max-height,padding] duration-250 ease-in-out overflow-hidden",
              "max-h-8 opacity-100 py-1.5",
              "group-data-[collapsible=icon]:max-h-0 group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:py-0",
            ].join(" ")}
          >
            Settings
          </p>
          <SidebarMenu className="gap-1 px-1 transition-[padding] duration-250 ease-in-out group-data-[collapsible=icon]:px-0">
            {navSettings.map((item) => (
              <SidebarMenuItem key={item.title}>
                <SidebarMenuButton
                  isActive={isActive(item.url)}
                  tooltip={item.title}
                  onClick={handleNavClick(item.url)}
                  className={[
                    "h-10 cursor-pointer rounded-lg text-[13px] transition-all duration-200",
                    "text-muted-foreground hover:text-foreground hover:bg-accent",
                    "data-[active=true]:bg-primary/10 data-[active=true]:text-primary data-[active=true]:font-medium data-[active=true]:shadow-sm data-[active=true]:!ring-0",
                    "group-data-[collapsible=icon]:w-10 group-data-[collapsible=icon]:h-10 group-data-[collapsible=icon]:!p-0 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:mx-auto group-data-[collapsible=icon]:!gap-0",
                  ].join(" ")}
                  render={<Link href={item.url} prefetch={false} />}
                >
                  <item.icon className="size-5 shrink-0" />
                  <span
                    className={[
                      "transition-[opacity,max-width,margin] duration-200 ease-in-out overflow-hidden whitespace-nowrap",
                      "group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:max-w-0 group-data-[collapsible=icon]:!ml-0",
                      "max-w-[160px] opacity-100 ml-2",
                    ].join(" ")}
                  >
                    {item.title}
                  </span>
                </SidebarMenuButton>
              </SidebarMenuItem>
            ))}
          </SidebarMenu>
        </div>
      </SidebarContent>

      {/* ── Footer — user info + logout ── */}
      <SidebarFooter className="border-t border-border p-3 transition-[padding] duration-250 ease-in-out group-data-[collapsible=icon]:p-0 group-data-[collapsible=icon]:py-3 group-data-[collapsible=icon]:items-center">
        {/* Fix: added w-full here to span the entire footer bar width */}
        <div className="flex w-full items-center gap-3 overflow-hidden transition-[gap] duration-250 ease-in-out group-data-[collapsible=icon]:gap-0">
          {/* Avatar */}
          <div className="flex items-center justify-center size-8 rounded-full bg-primary text-primary-foreground text-xs font-semibold shrink-0">
            {initials}
          </div>

          {/* User info */}
          <div
            className={[
              "flex flex-col leading-none text-left min-w-0 flex-1 overflow-hidden",
              "transition-[opacity,max-width] duration-200 ease-in-out",
              "group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:max-w-0",
              // Max width increased to allow text to fill the parent width instead of clipping early
              "max-w-[180px] opacity-100",
            ].join(" ")}
          >
            <span className="font-semibold text-[13px] truncate">
              {user?.full_name || "User"}
            </span>
            <span className="text-xs text-muted-foreground truncate">
              {user?.org_name || "Organization"}
            </span>
          </div>

          {/* Logout */}
          <button
            onClick={logout}
            className={[
              // Added ml-auto to push the logout to the hard right edge
              "ml-auto text-muted-foreground hover:text-red-400 transition-colors shrink-0 p-1.5 rounded-md hover:bg-accent",
              "transition-[opacity,max-width,padding] duration-200 ease-in-out overflow-hidden",
              "group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:max-w-0 group-data-[collapsible=icon]:p-0",
            ].join(" ")}
            title="Sign out"
          >
            <LogOut className="size-4" />
          </button>
        </div>
      </SidebarFooter>

      <SidebarRail />
    </Sidebar>
  );
}
