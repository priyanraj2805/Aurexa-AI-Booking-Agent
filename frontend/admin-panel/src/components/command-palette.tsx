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
  MessageSquare,
  Loader2,
} from "lucide-react";
import { useGlobalSearch } from "@/hooks/use-dashboard";
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
  const [query, setQuery] = useState("");
  const { results, loading, error } = useGlobalSearch(open ? query : "");
  const trimmed = query.trim();
  const hasResults =
    !!results && (results.bookings.length > 0 || results.conversations.length > 0);

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

  useEffect(() => {
    if (!open) setQuery("");
  }, [open]);

  const run = (fn: () => void) => {
    setOpen(false);
    fn();
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="hidden md:inline-flex h-9 w-28 lg:w-36 shrink-0 items-center gap-2 rounded-lg border border-border bg-background px-3 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground transition-colors cursor-pointer"
        aria-label="Open command menu"
      >
        <Search className="size-3.5 shrink-0" />
        <span className="hidden lg:inline min-w-0 flex-1 truncate text-left">Search…</span>
      </button>
      <button
        onClick={() => setOpen(true)}
        className="md:hidden inline-flex items-center justify-center size-9 shrink-0 rounded-lg border border-border bg-background text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
        aria-label="Open command menu"
      >
        <Search className="size-4" />
      </button>
      <CommandDialog open={open} onOpenChange={setOpen}>
        <Command className="p-1" shouldFilter={trimmed.length < 2}>
          <CommandInput
            placeholder="Search bookings, conversations or jump to a page…"
            value={query}
            onValueChange={setQuery}
          />
          <CommandList>
            {trimmed.length >= 2 && (
              <>
                {loading && (
                  <div className="flex items-center gap-2 px-3 py-4 text-sm text-muted-foreground">
                    <Loader2 className="size-3.5 animate-spin" /> Searching…
                  </div>
                )}
                {!loading && hasResults && (
                  <>
                    {results!.bookings.length > 0 && (
                      <CommandGroup heading="Bookings">
                        {results!.bookings.map((b) => (
                          <CommandItem
                            key={b.id}
                            value={`booking-${b.customer_name} ${b.customer_email}`}
                            onSelect={() =>
                              run(() =>
                                router.push(
                                  `/dashboard/bookings?search=${encodeURIComponent(
                                    b.customer_email || b.customer_name
                                  )}`
                                )
                              )
                            }
                          >
                            <CalendarCheck className="size-4 shrink-0" />
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-sm font-medium">{b.customer_name}</div>
                              <div className="truncate text-xs text-muted-foreground">
                                {b.customer_email}
                                {b.slot_start
                                  ? ` · ${new Date(b.slot_start).toLocaleDateString("en-US", {
                                      month: "short",
                                      day: "numeric",
                                    })}`
                                  : ""}
                              </div>
                            </div>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    )}
                    {results!.conversations.length > 0 && (
                      <CommandGroup heading="Conversations">
                        {results!.conversations.map((c) => (
                          <CommandItem
                            key={c.id}
                            value={`conversation-${c.customer_name ?? ""} ${c.customer_email ?? ""} ${c.id}`}
                            onSelect={() => run(() => router.push(`/dashboard/conversations?id=${c.id}`))}
                          >
                            <MessageSquare className="size-4 shrink-0" />
                            <div className="min-w-0 flex-1">
                              <div className="truncate text-sm font-medium">
                                {c.customer_name || c.customer_email || c.id.slice(0, 8)}
                              </div>
                              <div className="truncate text-xs text-muted-foreground capitalize">
                                {c.channel} · {c.session_status.toLowerCase()}
                              </div>
                            </div>
                          </CommandItem>
                        ))}
                      </CommandGroup>
                    )}
                  </>
                )}
                {!loading && error && (
                  <div className="px-3 py-4 text-center text-sm text-red-500">
                    Search failed: {error}
                  </div>
                )}
                {!loading && !error && !hasResults && trimmed.length >= 2 && (
                  <div className="px-3 py-4 text-center text-sm text-muted-foreground">
                    No matches for “{trimmed}”.
                  </div>
                )}
              </>
            )}
            {trimmed.length < 2 && <CommandEmpty>No results found.</CommandEmpty>}
            {NAV_GROUPS.map((group) => (
              <CommandGroup key={group.heading} heading={trimmed.length >= 2 ? "Pages" : group.heading}>
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
