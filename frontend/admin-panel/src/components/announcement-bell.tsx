"use client";

import * as React from "react";
import { Bell, Megaphone, X } from "lucide-react";
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

const POLL_MS = 30000;

const fmtTime = (s: string | null) => {
  if (!s) return "";
  const d = new Date(s);
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return d.toLocaleString();
};

export function AnnouncementBell() {
  const [unread, setUnread] = React.useState(0);
  const [open, setOpen] = React.useState(false);
  const [items, setItems] = React.useState<BroadcastItem[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [selected, setSelected] = React.useState<BroadcastItem | null>(null);
  const panelRef = React.useRef<HTMLDivElement>(null);

  const loadCount = React.useCallback(async () => {
    try {
      const r = await api.get<{ unread_count: number }>("/broadcasts/unread-count");
      setUnread(r.unread_count);
    } catch (err) {
      // Never swallow badge-poll failures silently — surface them so wiring
      // bugs (missing import, wrong endpoint) are visible.
      console.error("[announcement-bell] failed to load unread count", err);
    }
  }, []);

  const loadItems = React.useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<{ items: BroadcastItem[] }>("/broadcasts");
      setItems(r.items);
      setUnread(r.unread_count ?? 0);
    } catch (err) {
      // Never swallow badge-poll failures silently — surface them so wiring
      // bugs (missing import, wrong endpoint) are visible.
      console.error("[announcement-bell] failed to load announcements", err);
    } finally {
      setLoading(false);
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
      await Promise.all(
        items
          .filter((i) => !i.read)
          .map((i) => api.post(`/broadcasts/${i.id}/read`))
      );
      setUnread(0);
      setItems((prev) => prev.map((i) => ({ ...i, read: true })));
    } catch (err) {
      console.error("[announcement-bell] mark all read failed", err);
    }
  };

  return (
    <div className="relative" ref={panelRef}>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setOpen((prev) => !prev)}
        aria-label={`Announcements (${unread} unread)`}
        className="relative text-muted-foreground hover:text-foreground"
      >
        <Bell className="size-5" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-bold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </Button>

      {open && (
        <div className="absolute right-0 top-11 z-50 w-80 rounded-lg border border-border bg-background shadow-xl">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <span className="text-sm font-semibold">Announcements</span>
            {items.some((i) => !i.read) && (
              <button
                onClick={markAll}
                className="text-xs text-primary hover:opacity-80"
              >
                Mark all read
              </button>
            )}
          </div>
          <div className="max-h-80 overflow-y-auto">
            {loading && items.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                Loading…
              </p>
            )}
            {!loading && items.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                No announcements yet
              </p>
            )}
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
