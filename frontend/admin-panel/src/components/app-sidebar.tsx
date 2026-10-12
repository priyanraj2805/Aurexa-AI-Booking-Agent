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
  LifeBuoy,
  CreditCard,
  Settings,
  Sun,
  Moon,
  Palette,
  Image as ImageIcon,
  Upload,
  Trash2,
  Link as LinkIcon,
  Building2,
  Users,
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
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
} from "@/components/ui/dropdown-menu";
import { useAuth } from "@/lib/auth-context";
import { useTheme } from "next-themes";
import { uploadFile } from "@/lib/api";
import { toast } from "sonner";

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

const navOrg = [
  { title: "Departments", url: "/dashboard/departments", icon: Building2 },
  { title: "Staff", url: "/dashboard/staff", icon: Users },
];

/** Display name that never repeats the email (some accounts saved email as name). */
function displayName(user: { full_name?: string | null; email?: string | null } | null): string {
  const raw = (user?.full_name || "").trim();
  if (raw && !raw.includes("@")) return raw;
  const prefix = (user?.email || "").split("@")[0].replace(/[._-]+/g, " ").trim();
  if (prefix) return prefix.charAt(0).toUpperCase() + prefix.slice(1);
  return "User";
}

export function AppSidebar({ ...props }: React.ComponentProps<typeof Sidebar>) {
  const { user, logout, refresh, isDoctor, scope } = useAuth();
  // Linked member's departments for the badge (e.g. Cardiology +2).
  const scopeDeptNames = scope?.provider_unit_names?.length
    ? scope.provider_unit_names
    : scope?.unit_names || [];
  const deptBadge = scopeDeptNames.length > 1
    ? `${scopeDeptNames[0]} +${scopeDeptNames.length - 1}`
    : scopeDeptNames[0] || "";
  const { theme, setTheme } = useTheme();
  const pathname = usePathname();
  const router = useRouter();
  const { isMobile, setOpenMobile, openMobile } = useSidebar();
  const [confirmLogout, setConfirmLogout] = React.useState(false);
  const avatarInputRef = React.useRef<HTMLInputElement>(null);
  const [avatarUploading, setAvatarUploading] = React.useState(false);

  const openLogoutDialog = () => {
    setConfirmLogout(true);
  };

  const handleAvatarFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      toast.error("Please select an image file");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      toast.error("Image must be under 2MB");
      return;
    }
    setAvatarUploading(true);
    try {
      await uploadFile("/account/avatar", file);
      toast.success("Avatar updated");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setAvatarUploading(false);
      if (avatarInputRef.current) avatarInputRef.current.value = "";
    }
  };

  const handleAvatarRemove = async () => {
    setAvatarUploading(true);
    try {
      const { api } = await import("@/lib/api");
      await api.del("/account/avatar");
      toast.success("Avatar removed");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to remove");
    } finally {
      setAvatarUploading(false);
    }
  };

  const handleAvatarUrl = async () => {
    const url = window.prompt("Paste image URL (https://...)");
    if (!url) return;
    try {
      new URL(url);
    } catch {
      toast.error("Invalid URL");
      return;
    }
    setAvatarUploading(true);
    try {
      const { api } = await import("@/lib/api");
      await api.put("/account/avatar-url", { avatar_url: url.trim() });
      toast.success("Avatar URL saved");
      await refresh();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save URL");
    } finally {
      setAvatarUploading(false);
    }
  };

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

  // Linked logins show the member name the org admin entered in Departments,
  // not the login account's own name.
  const shownName =
    isDoctor && scope?.provider_name ? scope.provider_name : displayName(user);
  const initials = (() => {
    return (
      shownName
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2) || "DS"
    );
  })();

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

        {/* ── Settings Section — hidden for linked doctors (org manages it) ── */}
        {!isDoctor && (
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
        )}

        {/* ── Organization Section — hospital malls only, never for linked doctors ── */}
        {(user as unknown as { org_type?: string })?.org_type === "organization" && !isDoctor && (
          <div className="mt-1">
            <p
              className={[
                "text-[11px] font-semibold uppercase tracking-wider text-muted-foreground px-2",
                "transition-[opacity,max-height,padding] duration-250 ease-in-out overflow-hidden",
                "max-h-8 opacity-100 py-1.5",
                "group-data-[collapsible=icon]:max-h-0 group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:py-0",
              ].join(" ")}
            >
              Organization
            </p>
            <SidebarMenu className="gap-1 px-1 transition-[padding] duration-250 ease-in-out group-data-[collapsible=icon]:px-0">
              {navOrg.map((item) => (
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
        )}
      </SidebarContent>

      {/* ── Footer — user dropdown (settings + sign out) ── */}
      <SidebarFooter className="border-t border-border p-3 transition-[padding] duration-250 ease-in-out group-data-[collapsible=icon]:p-0 group-data-[collapsible=icon]:py-3 group-data-[collapsible=icon]:items-center">
        <DropdownMenu>
          <DropdownMenuTrigger
            className={[
              "flex w-full cursor-pointer items-center gap-3 overflow-hidden rounded-md p-1.5 text-left transition-colors hover:bg-accent",
              "transition-[gap] duration-250 ease-in-out group-data-[collapsible=icon]:gap-0 group-data-[collapsible=icon]:justify-center group-data-[collapsible=icon]:hover:bg-transparent",
            ].join(" ")}
            title="Account menu"
          >
            {/* Avatar — image if uploaded, else initials */}
            <div className="flex items-center justify-center size-8 rounded-full bg-primary text-primary-foreground text-xs font-semibold shrink-0 overflow-hidden">
              {user?.avatar_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={user.avatar_url}
                  alt={user.full_name || "User"}
                  className="size-full object-cover"
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = "none";
                  }}
                />
              ) : (
                initials
              )}
            </div>

            {/* User info */}
            <div
              className={[
                "flex flex-col leading-none text-left min-w-0 flex-1 overflow-hidden",
                "transition-[opacity,max-width] duration-200 ease-in-out",
                "group-data-[collapsible=icon]:opacity-0 group-data-[collapsible=icon]:max-w-0",
                "max-w-[180px] opacity-100",
              ].join(" ")}
            >
              <span className="font-semibold text-[13px] truncate">
                {shownName}
              </span>
              <span className="text-xs text-muted-foreground truncate">
                {user?.org_name || "Organization"}
              </span>
              <span
                className={[
                  "mt-1 w-fit text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded",
                  (user as unknown as { org_type?: string })?.org_type === "organization"
                    ? "bg-primary/15 text-primary"
                    : "bg-secondary text-muted-foreground",
                ].join(" ")}
              >
                {isDoctor
                  ? (deptBadge || "Team member")
                  : (user as unknown as { org_type?: string })?.org_type === "organization"
                    ? "🏥 Organization"
                    : "🧍 Individual"}
              </span>
            </div>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" side="top" className="w-52">
            <DropdownMenuGroup>
              <DropdownMenuLabel className="font-normal">
                <span className="block truncate text-sm font-semibold text-foreground">
                  {shownName}
                </span>
                <span className="block truncate text-xs text-muted-foreground">
                  {user?.email}
                </span>
              </DropdownMenuLabel>
            </DropdownMenuGroup>
            <DropdownMenuSeparator />
            {(user?.permissions || []).some((p) => p === "settings.view" || p === "settings.manage") && (
              <DropdownMenuItem
                render={<Link href="/dashboard/settings" />}
                className="cursor-pointer"
              >
                <Settings className="size-4" />
                Settings
              </DropdownMenuItem>
            )}
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="cursor-pointer">
                <ImageIcon className="size-4" />
                Avatar
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                <DropdownMenuItem
                  onClick={() => avatarInputRef.current?.click()}
                  className="cursor-pointer"
                  disabled={avatarUploading}
                >
                  <Upload className="size-4" />
                  Upload image
                </DropdownMenuItem>
                <DropdownMenuItem onClick={handleAvatarUrl} className="cursor-pointer" disabled={avatarUploading}>
                  <LinkIcon className="size-4" />
                  Via URL
                </DropdownMenuItem>
                {user?.avatar_url && (
                  <DropdownMenuItem
                    onClick={handleAvatarRemove}
                    className="cursor-pointer text-destructive focus:text-destructive"
                    disabled={avatarUploading}
                  >
                    <Trash2 className="size-4" />
                    Remove
                  </DropdownMenuItem>
                )}
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            {(user?.permissions || []).includes("billing.view") && (
              <DropdownMenuItem
                render={<Link href="/dashboard/billing" />}
                className="cursor-pointer"
              >
                <CreditCard className="size-4" />
                Billing
              </DropdownMenuItem>
            )}
            <DropdownMenuItem
              render={<Link href="/dashboard/support" />}
              className="cursor-pointer"
            >
              <LifeBuoy className="size-4" />
              Support
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuSub>
              <DropdownMenuSubTrigger className="cursor-pointer">
                <Palette className="size-4" />
                Theme
              </DropdownMenuSubTrigger>
              <DropdownMenuSubContent>
                <DropdownMenuItem
                  onClick={() => setTheme("light")}
                  className={theme === "light" ? "font-medium bg-accent" : ""}
                >
                  <Sun className="size-3.5" />
                  White theme
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => setTheme("dark")}
                  className={theme === "dark" ? "font-medium bg-accent" : ""}
                >
                  <Moon className="size-3.5" />
                  Dark theme
                </DropdownMenuItem>
              </DropdownMenuSubContent>
            </DropdownMenuSub>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={openLogoutDialog}
              className="cursor-pointer text-destructive focus:text-destructive"
            >
              <LogOut className="size-4" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <input
          ref={avatarInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/svg+xml"
          className="hidden"
          onChange={handleAvatarFile}
        />
      </SidebarFooter>

      <SidebarRail />
      <AlertDialog open={confirmLogout} onOpenChange={setConfirmLogout}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Sign out</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to sign out of your admin panel?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-col gap-2 sm:flex-row">
            <AlertDialogCancel onClick={() => setConfirmLogout(false)}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                setConfirmLogout(false);
                logout();
              }}
            >
              Sign out
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Sidebar>
  );
}
