"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, CheckCheck } from "lucide-react";
import { api } from "@/lib/api";

type InboxItem = {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  resource_type: string | null;
  resource_id: string | null;
  is_read: boolean;
  created_at: string;
  read_at: string | null;
};

const POLL_MS = 30000;

export function NotificationBell() {
  const router = useRouter();
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<InboxItem[]>([]);
  const [loading, setLoading] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);

  const loadCount = useCallback(async () => {
    try {
      const r = await api.get<{ unread: number }>("/inbox/unread-count");
      setUnread(r.unread);
    } catch {
      // transient — keep the previous badge
    }
  }, []);

  const loadItems = useCallback(async () => {
    setLoading(true);
    try {
      const r = await api.get<{ items: InboxItem[] }>("/inbox?limit=20");
      setItems(r.items);
    } catch {
      // transient — keep the previous list
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadCount();
    const t = setInterval(loadCount, POLL_MS);
    const onFocus = () => void loadCount();
    window.addEventListener("focus", onFocus);
    return () => {
      clearInterval(t);
      window.removeEventListener("focus", onFocus);
    };
  }, [loadCount]);

  useEffect(() => {
    if (!open) return;
    void loadItems();
    void loadCount();
  }, [open, loadItems, loadCount]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const openItem = async (item: InboxItem) => {
    setOpen(false);
    if (!item.is_read) {
      await api.post(`/inbox/${item.id}/read`).catch(() => {});
      setUnread((prev) => Math.max(0, prev - 1));
    }
    if (item.kind.startsWith("impersonation")) {
      window.sessionStorage.setItem("imp:toast", item.resource_id ?? "");
      router.push(
        `/dashboard/support?impersonation=${encodeURIComponent(item.resource_id ?? "")}&imp_t=${Date.now()}`
      );
    } else if (item.kind.startsWith("peek")) {
      router.push("/dashboard/support");
    }
  };

  const markAll = async () => {
    await api.post("/inbox/read-all").catch(() => {});
    setUnread(0);
    setItems((prev) => prev.map((i) => ({ ...i, is_read: true })));
  };

  const fmtTime = (s: string) => {
    const d = new Date(s);
    const diff = Date.now() - d.getTime();
    if (diff < 60_000) return "just now";
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
    return d.toLocaleString();
  };

  return (
    <div className="relative" ref={panelRef}>
      <button
        onClick={() => setOpen((prev) => !prev)}
        aria-label={`Notifications (${unread} unread)`}
        className="relative rounded-lg p-2 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
      >
        <Bell className="h-6 w-6" />
        {unread > 0 && (
          <span className="absolute -right-1 -top-1 flex h-5 min-w-5 items-center justify-center rounded-full bg-red-600 px-1.5 text-xs font-bold text-white">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 top-11 z-50 w-80 rounded-lg border border-slate-700 bg-slate-900 shadow-xl">
          <div className="flex items-center justify-between border-b border-slate-800 px-3 py-2">
            <span className="text-sm font-semibold">Notifications</span>
            {items.some((i) => !i.is_read) && (
              <button
                onClick={markAll}
                className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300"
              >
                <CheckCheck className="h-3.5 w-3.5" /> Mark all read
              </button>
            )}
          </div>
          <div className="max-h-80 overflow-y-auto">
            {loading && items.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-slate-500">
                Loading…
              </p>
            )}
            {!loading && items.length === 0 && (
              <p className="px-3 py-6 text-center text-sm text-slate-500">
                No notifications yet
              </p>
            )}
            {items.map((item) => (
              <button
                key={item.id}
                onClick={() => void openItem(item)}
                className={`block w-full border-b border-slate-800 px-3 py-2.5 text-left hover:bg-slate-800/60 ${
                  item.is_read ? "opacity-70" : ""
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="text-sm font-medium text-slate-100">
                    {item.title}
                  </span>
                  {!item.is_read && (
                    <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-indigo-500" />
                  )}
                </div>
                {item.body && (
                  <p className="mt-0.5 text-xs text-slate-400">{item.body}</p>
                )}
                <p className="mt-1 text-[10px] uppercase tracking-wide text-slate-500">
                  {fmtTime(item.created_at)}
                </p>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
