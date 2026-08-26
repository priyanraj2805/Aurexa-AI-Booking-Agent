"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import {
  LayoutDashboard,
  Inbox,
  CalendarCheck,
  CalendarX2,
  CalendarDays,
  Bot,
  Clock,
  Plug,
  CreditCard,
  LifeBuoy,
  Settings,
  Moon,
  Sun,
  Search,
} from "lucide-react";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command";

const NAV_GROUPS = [
  {
    heading: "App",
    items: [
      { label: "Dashboard", icon: LayoutDashboard, href: "/dashboard" },
      { label: "Inbox", icon: Inbox, href: "/dashboard/conversations" },
      { label: "Bookings", icon: CalendarCheck, href: "/dashboard/bookings" },
      { label: "Cancelled Bookings", icon: CalendarX2, href: "/dashboard/bookings/cancelled" },
      { label: "Calendar", icon: CalendarDays, href: "/dashboard/calendar" },
      { label: "Billing", icon: CreditCard, href: "/dashboard/billing" },
    ],
  },
  {
    heading: "Settings",
    items: [
      { label: "Agent Personalisation", icon: Bot, href: "/dashboard/agent" },
      { label: "Business Hours", icon: Clock, href: "/dashboard/business-hours" },
      { label: "Integration", icon: Plug, href: "/dashboard/integrations" },
      { label: "Support", icon: LifeBuoy, href: "/dashboard/support" },
      { label: "Profile Settings", icon: Settings, href: "/dashboard/settings" },
    ],
  },
];

export function CommandPalette() {
  const router = useRouter();
  const { resolvedTheme, setTheme } = useTheme();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    document.addEventListener("keydown", down);
    return () => document.removeEventListener("keydown", down);
  }, []);

  const run = (fn: () => void) => {
    setOpen(false);
    fn();
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="hidden sm:inline-flex items-center gap-2 h-8 rounded-lg border border-border bg-background px-3 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground transition-colors cursor-pointer"
        aria-label="Open command menu"
      >
        <Search className="size-3.5" />
        <span>Search…</span>
        <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-0.5 rounded border border-border bg-secondary px-1.5 font-mono text-[10px] font-medium">
          Ctrl K
        </kbd>
      </button>
      <button
        onClick={() => setOpen(true)}
        className="sm:hidden inline-flex items-center justify-center size-8 rounded-lg border border-border bg-background text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        aria-label="Open command menu"
      >
        <Search className="size-4" />
      </button>
      <CommandDialog open={open} onOpenChange={setOpen}>
        <Command className="p-1">
          <CommandInput placeholder="Type a command or search…" />
          <CommandList>
            <CommandEmpty>No results found.</CommandEmpty>
            {NAV_GROUPS.map((group) => (
              <CommandGroup key={group.heading} heading={group.heading}>
                {group.items.map((item) => (
                  <CommandItem
                    key={item.href}
                    onSelect={() => run(() => router.push(item.href))}
                  >
                    <item.icon className="size-4" />
                    <span>{item.label}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
            <CommandSeparator />
            <CommandGroup heading="Preferences">
              <CommandItem onSelect={() => run(() => setTheme(resolvedTheme === "light" ? "dark" : "light"))}>
                {resolvedTheme === "light" ? (
                  <Moon className="size-4" />
                ) : (
                  <Sun className="size-4" />
                )}
                <span>Toggle {resolvedTheme === "light" ? "dark" : "light"} mode</span>
              </CommandItem>
            </CommandGroup>
          </CommandList>
        </Command>
      </CommandDialog>
    </>
  );
}
