"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Bell, CalendarCheck2, CalendarX2, CreditCard, Megaphone, X } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

type BroadcastItem = {
  id: string;
  title: string;
  body: string;
  sent_at: string | null;
  read: boolean;
};

type InboxItem = {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  resource_type: string | null;
  resource_id: string | null;
  is_read: boolean;
  created_at: string | null;
  read_at: string | null;
};

const POLL_MS = 30000;

const fmtTime = (s: string | null) => {
  if (!s) return "";
  const d = new Date(s);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  return d.toLocaleString();
};

export function AnnouncementBell() {
  const router = useRouter();
  const [unread, setUnread] = React.useState(0);
  const [open, setOpen] = React.useState(false);
  const [items, setItems] = React.useState<BroadcastItem[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [selected, setSelected] = React.useState<BroadcastItem | null>(null);
  const panelRef = React.useRef<HTMLDivElement>(null);

  // Booking activity notifications
  const [tab, setTab] = React.useState<"announcements" | "bookings">("announcements");
  const [bookingItems, setBookingItems] = React.useState<InboxItem[]>([]);
  const [bookingUnread, setBookingUnread] = React.useState(0);
  // Billing notices have their own server-side count (they live under Announcements)
  const [billingUnread, setBillingUnread] = React.useState(0);

  // Billing notices (plan expiry, subscription) belong to Announcements,
  // not booking activity. Partitioned client-side from the same inbox feed.
  const isBillingItem = (i: InboxItem) =>
    i.kind === "billing_expiry" ||
    i.kind.startsWith("billing_") ||
    i.resource_type === "subscription";
  const billingItems = bookingItems.filter(isBillingItem);
  const activityItems = bookingItems.filter((i) => !isBillingItem(i));

  const loadCount = React.useCallback(async () => {
    try {
      const r = await api.get<{ unread_count: number }>("/broadcasts/unread-count");
      setUnread(r.unread_count);
    } catch (err) {
      console.error("[announcement-bell] failed to load unread count", err);
    }
    try {
      const r = await api.get<{ unread: number }>(
        "/dashboard/notifications/unread-count?exclude_billing=true"
      );
      setBookingUnread(r.unread);
    } catch (err) {
      console.error("[announcement-bell] failed to load booking unread count", err);
    }
    try {
      const r = await api.get<{ unread: number }>(
        "/dashboard/notifications/unread-count?billing_only=true"
      );
      setBillingUnread(r.unread);
    } catch (err) {
      console.error("[announcement-bell] failed to load billing unread count", err);
    }
  }, []);

  const loadItems = React.useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<{ items: BroadcastItem[] }>("/broadcasts");
      setItems(r.items);
      setUnread(r.unread_count ?? 0);
    } catch (err) {
      console.error("[announcement-bell] failed to load announcements", err);
    } finally {
      setLoading(false);
    }
    try {
      const r = await api.get<{ items: InboxItem[] }>(
        "/dashboard/notifications?limit=20"
      );
      setBookingItems(r.items);
    } catch (err) {
      console.error("[announcement-bell] failed to load booking notifications", err);
    }
  }, []);

  React.useEffect(() => {
    void loadCount();
    const t = setInterval(loadCount, POLL_MS);
    const onFocus = () => void loadCount();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", onFocus);
    };
  }, [loadCount]);

  React.useEffect(() => {
    if (!open) return;
    void loadItems();
    void loadCount();
  }, [open, loadItems, loadCount]);

  React.useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  React.useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelected(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [selected]);

  const openItem = async (item: BroadcastItem) => {
    setOpen(false);
    if (!item.read) {
      await api
        .post(`/broadcasts/${item.id}/read`)
        .catch((err) => console.error("[announcement-bell] mark read failed", err));
      setItems((prev) => prev.map((i) => (i.id === item.id ? { ...i, read: true } : i)));
      setUnread((prev) => Math.max(0, prev - 1));
    }
    setSelected(item);
  };

  const markAll = async () => {
    try {
      const unreadBilling = billingItems.filter((i) => !i.is_read);
      await Promise.all([
        ...items
          .filter((i) => !i.read)
          .map((i) => api.post(`/broadcasts/${i.id}/read`)),
        ...unreadBilling.map((i) =>
          api.post(`/dashboard/notifications/${i.id}/read`)
        ),
      ]);
      setUnread(0);
      setItems((prev) => prev.map((i) => ({ ...i, read: true })));
      if (unreadBilling.length > 0) {
        const ids = new Set(unreadBilling.map((i) => i.id));
        setBookingItems((prev) =>
          prev.map((i) => (ids.has(i.id) ? { ...i, is_read: true } : i))
        );
        setBillingUnread((prev) => Math.max(0, prev - unreadBilling.length));
      }
    } catch (err) {
      console.error("[announcement-bell] mark all read failed", err);
    }
  };

  const openBillingItem = async (item: InboxItem) => {
    setOpen(false);
    if (!item.is_read) {
      await api
        .post(`/dashboard/notifications/${item.id}/read`)
        .catch((err) =>
          console.error("[announcement-bell] billing mark read failed", err)
        );
      setBookingItems((prev) =>
        prev.map((i) => (i.id === item.id ? { ...i, is_read: true } : i))
      );
      setBillingUnread((prev) => Math.max(0, prev - 1));
    }
    router.push("/dashboard/billing");
  };

  const openBookingItem = async (item: InboxItem) => {
    setOpen(false);
    if (isBillingItem(item)) {
      await openBillingItem(item);
      return;
    }
    if (!item.is_read) {
      await api
        .post(`/dashboard/notifications/${item.id}/read`)
        .catch((err) =>
          console.error("[announcement-bell] booking mark read failed", err)
        );
      setBookingItems((prev) =>
        prev.map((i) => (i.id === item.id ? { ...i, is_read: true } : i))
      );
      setBookingUnread((prev) => Math.max(0, prev - 1));
    }
    const highlight = item.resource_id ? `?highlight=${encodeURIComponent(item.resource_id)}` : "";
    if (item.kind === "booking_cancelled") {
      router.push(`/dashboard/bookings/cancelled${highlight}`);
    } else if (item.resource_type === "booking" && item.resource_id) {
      router.push(`/dashboard/bookings${highlight}`);
    } else if ((item.resource_type === "conversation" || item.resource_type === "session") && item.resource_id) {
      router.push(`/dashboard/conversations?highlight=${encodeURIComponent(item.resource_id)}`);
    } else if (item.resource_id) {
      // Fallback: try bookings highlight, else conversations
      router.push(`/dashboard/bookings${highlight}`);
    } else {
      router.push("/dashboard/bookings");
    }
  };

  const markAllBookings = async () => {
    // Per-item over booking activity only: the bulk read-all endpoint would
    // also clear billing notices, which now live under Announcements.
    try {
      const unreadActivity = activityItems.filter((i) => !i.is_read);
      await Promise.all(
        unreadActivity.map((i) => api.post(`/dashboard/notifications/${i.id}/read`))
      );
      if (unreadActivity.length > 0) {
        const ids = new Set(unreadActivity.map((i) => i.id));
        setBookingItems((prev) =>
          prev.map((i) => (ids.has(i.id) ? { ...i, is_read: true } : i))
        );
        setBookingUnread((prev) => Math.max(0, prev - unreadActivity.length));
      }
    } catch (err) {
      console.error("[announcement-bell] booking mark all read failed", err);
    }
  };

  const totalUnread = unread + bookingUnread + billingUnread;

  return (
    <div className="relative" ref={panelRef}>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen((prev) => !prev)}
        aria-label={`Notifications (${totalUnread} unread)`}
        className="relative text-muted-foreground hover:text-foreground"
      >
        <Bell className="size-5" />
        {totalUnread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-bold text-white">
            {totalUnread > 99 ? "99+" : totalUnread}
          </span>
        )}
      </Button>

      {open && (
        <div className="absolute right-0 top-11 z-50 w-80 rounded-lg border border-border bg-background shadow-xl">
          <div className="flex items-center border-b border-border px-1.5 pt-1.5">
            <button
              onClick={() => setTab("announcements")}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-t-md px-3 py-2 text-xs font-medium transition-colors ${
                tab === "announcements"
                  ? "border-b-2 border-primary text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Megaphone className="size-3.5" />
              Announcements
              {unread + billingUnread > 0 && (
                <span className="rounded-full bg-red-600 px-1.5 text-[10px] font-bold text-white">
                  {unread + billingUnread}
                </span>
              )}
            </button>
            <button
              onClick={() => setTab("bookings")}
              className={`flex flex-1 items-center justify-center gap-1.5 rounded-t-md px-3 py-2 text-xs font-medium transition-colors ${
                tab === "bookings"
                  ? "border-b-2 border-primary text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <CalendarCheck2 className="size-3.5" />
              Bookings
              {bookingUnread > 0 && (
                <span className="rounded-full bg-red-600 px-1.5 text-[10px] font-bold text-white">
                  {bookingUnread}
                </span>
              )}
            </button>
          </div>

          {tab === "announcements" && (
            <>
              <div className="max-h-80 overflow-y-auto">
                {loading && items.length === 0 && billingItems.length === 0 && (
                  <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                    Loading…
                  </p>
                )}
                {!loading && items.length === 0 && billingItems.length === 0 && (
                  <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                    No announcements yet
                  </p>
                )}
                {billingItems.map((item) => (
                  <button
                    key={`billing-${item.id}`}
                    onClick={() => void openBillingItem(item)}
                    className={`flex w-full items-start gap-2.5 border-b border-border px-3 py-2.5 text-left hover:bg-accent ${
                      item.is_read ? "opacity-60" : ""
                    }`}
                  >
                    <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-amber-500/10 text-amber-500">
                      <CreditCard className="size-3.5" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium">
                          {item.title}
                        </span>
                        {!item.is_read && (
                          <Badge variant="destructive" className="px-1.5 text-[10px]">
                            New
                          </Badge>
                        )}
                      </span>
                      {item.body && (
                        <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">
                          {item.body}
                        </span>
                      )}
                      <span className="mt-1 block text-[10px] uppercase tracking-wide text-muted-foreground/70">
                        {fmtTime(item.created_at)}
                      </span>
                    </span>
                  </button>
                ))}
                {items.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => void openItem(item)}
                    className={`block w-full border-b border-border px-3 py-2.5 text-left hover:bg-accent ${
                      item.read ? "opacity-70" : ""
                    }`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="flex items-center gap-1.5 text-sm font-medium">
                        <Megaphone className="size-3.5 shrink-0 text-primary" />
                        {item.title}
                      </span>
                      {!item.read && (
                        <Badge variant="destructive" className="px-1.5 text-[10px]">
                          New
                        </Badge>
                      )}
                    </div>
                    {item.body && (
                      <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
                        {item.body}
                      </p>
                    )}
                    <p className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground/70">
                      {fmtTime(item.sent_at)}
                    </p>
                  </button>
                ))}
              </div>
              {(items.some((i) => !i.read) ||
                billingItems.some((i) => !i.is_read)) && (
                <div className="border-t border-border px-3 py-2">
                  <button
                    onClick={markAll}
                    className="text-xs text-primary hover:opacity-80"
                  >
                    Mark all read
                  </button>
                </div>
              )}
            </>
          )}

          {tab === "bookings" && (
            <>
              <div className="max-h-80 overflow-y-auto scrollbar-thin">
                {loading && activityItems.length === 0 && (
                  <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                    Loading…
                  </p>
                )}
                {!loading && activityItems.length === 0 && (
                  <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                    No booking activity yet
                  </p>
                )}
                {activityItems.map((item) => {
                  const Icon =
                    item.kind === "booking_cancelled" ? CalendarX2 : CalendarCheck2;
                  const iconCls =
                    item.kind === "booking_cancelled"
                      ? "bg-red-500/10 text-red-500"
                      : "bg-green-500/10 text-green-500";
                  return (
                    <button
                      key={item.id}
                      onClick={() => void openBookingItem(item)}
                      className={`flex w-full items-start gap-2.5 border-b border-border px-3 py-2.5 text-left hover:bg-accent ${
                        item.is_read ? "opacity-60" : ""
                      }`}
                    >
                      <span
                        className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg ${iconCls}`}
                      >
                        <Icon className="size-3.5" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-medium">
                            {item.title}
                          </span>
                          {!item.is_read && (
                            <Badge variant="destructive" className="px-1.5 text-[10px]">
                              New
                            </Badge>
                          )}
                        </span>
                        {item.body && (
                          <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">
                            {item.body}
                          </span>
                        )}
                        <span className="mt-1 block text-[10px] uppercase tracking-wide text-muted-foreground/70">
                          {fmtTime(item.created_at)}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
              {activityItems.some((i) => !i.is_read) && (
                <div className="border-t border-border px-3 py-2">
                  <button
                    onClick={markAllBookings}
                    className="text-xs text-primary hover:opacity-80"
                  >
                    Mark all read
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {selected && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
          onClick={() => setSelected(null)}
        >
          <div
            className="flex max-h-[80vh] w-full max-w-lg flex-col rounded-lg border border-border bg-background shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
              <div className="flex items-center gap-2">
                <Megaphone className="size-4 shrink-0 text-primary" />
                <h2 className="text-sm font-semibold">{selected.title}</h2>
              </div>
              <button
                onClick={() => setSelected(null)}
                aria-label="Close announcement"
                className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="overflow-y-auto px-4 py-3">
              <p className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground/70">
                {selected.sent_at
                  ? new Date(selected.sent_at).toLocaleString()
                  : "No date"}
              </p>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                {selected.body || "No message content."}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
