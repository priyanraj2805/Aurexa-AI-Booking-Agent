"use client";

import { useState, useRef, useEffect, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { Loader2, AlertCircle, ArrowLeft, User, Bot, Globe, MessageCircle, Headset, Bot as BotIcon, Send, Search, ChevronDown, ChevronUp, Calendar, ArrowUpDown } from "lucide-react";
import { useConversations, useTranscript, takeoverConversation, releaseConversation, sendHumanMessage } from "@/hooks/use-dashboard";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { Pagination } from "@/components/pagination";
import { toast } from "sonner";
import { SearchInput, CheckboxFilter, DateFilter, SortableTh } from "@/components/filter-bar";

function statusBadge(status: string) {
  switch (status) {
    case "CONFIRMED":
      return "bg-green-500/10 text-green-500";
    case "ACTIVE":
    case "CONFIRMING":
      return "bg-yellow-500/10 text-yellow-500";
    case "CANCELLED":
      return "bg-red-500/10 text-red-500";
    default:
      return "bg-white/10 text-muted-foreground";
  }
}

function ChannelBadge({ channel }: { channel: string }) {
  const isWhatsApp = channel === "whatsapp" || channel === "whatsapp_cloud";
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-medium px-2 py-1 rounded-full ${
      isWhatsApp 
        ? "bg-green-500/10 text-green-500" 
        : "bg-blue-500/10 text-blue-500"
    }`}>
      {isWhatsApp ? (
        <>
          <MessageCircle className="size-3" />
          WhatsApp
        </>
      ) : (
        <>
          <Globe className="size-3" />
          Web
        </>
      )}
    </span>
  );
}

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString([], {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function deliveryLabel(status?: string | null): string | null {
  switch (status) {
    case "GENERATED":
      return "Generated";
    case "SENT":
      return "Sent";
    case "DELIVERED":
      return "Delivered";
    case "READ":
      return "Read";
    case "FAILED":
      return "Failed";
    default:
      return null;
  }
}

function deliveryBadgeClass(status?: string | null): string {
  switch (status) {
    case "DELIVERED":
    case "READ":
      return "text-green-500";
    case "FAILED":
      return "text-red-500";
    case "SENT":
      return "text-blue-500";
    default:
      return "text-muted-foreground";
  }
}

// Build a display identity for a session: prefer collected contact info,
// fall back to the session ID when nothing was collected yet.
function customerIdentity(s: {
  customer_name?: string | null;
  customer_email?: string | null;
  customer_phone?: string | null;
  id: string;
}): { title: string; subtitle: string | null; hasContact: boolean } {
  const name = s.customer_name?.trim();
  const email = s.customer_email?.trim();
  const phone = s.customer_phone?.trim();
  const contact = email || phone;
  if (name && contact) {
    return { title: name, subtitle: contact, hasContact: true };
  }
  if (contact) {
    return { title: contact, subtitle: name || null, hasContact: true };
  }
  if (name) {
    return { title: name, subtitle: null, hasContact: false };
  }
  return { title: s.id.slice(0, 8) + "…", subtitle: "Session", hasContact: false };
}

function resolveTranscriptText(raw: unknown): string {
  if (typeof raw !== "string") return "";
  const trimmed = raw.trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
    try {
      const parsed = JSON.parse(trimmed) as Record<string, unknown>;
      const preferred = parsed.response_text ?? parsed.response_message ?? parsed.message ?? parsed.text;
      if (typeof preferred === "string" && preferred.trim()) return preferred.trim();
    } catch {
      // fall through to raw text
    }
  }
  return trimmed;
}

// ── Transcript Detail View ───────────────────────
function TranscriptPanel({
  sessionId,
  onBack,
}: {
  sessionId: string;
  onBack: () => void;
}) {
  const { user } = useAuth();
  const { data: messages, loading, error, refetch } = useTranscript(sessionId);
  const [humanActive, setHumanActive] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [pendingMessage, setPendingMessage] = useState("");
  const [sendingMessage, setSendingMessage] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const prevMessageCountRef = useRef(0);
  const forceScrollRef = useRef(false);
  const orgId = user?.org_id ?? "";
  const searchParams = useSearchParams();
  const highlightMessageId = searchParams.get("highlight");
  const [highlightedMessageId, setHighlightedMessageId] = useState<string | null>(null);

  useEffect(() => {
    const h = searchParams.get("highlight");
    if (h) {
      setHighlightedMessageId(h);
      const t = setTimeout(() => setHighlightedMessageId(null), 3500);
      return () => clearTimeout(t);
    }
  }, [searchParams]);

  useEffect(() => {
    if (!highlightedMessageId || !messages) return;
    const t = setTimeout(() => {
      const el = document.querySelector(`[data-message-id="${highlightedMessageId}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 400);
    return () => clearTimeout(t);
  }, [highlightedMessageId, messages]);

  // Auto-scroll to bottom only when appropriate (don't yank user reading history)
  useEffect(() => {
    if (!messages || messages.length === 0) {
      prevMessageCountRef.current = 0;
      return;
    }
    const isInitial = prevMessageCountRef.current === 0;
    const hasNewMessages = messages.length > prevMessageCountRef.current;
    const prevCount = prevMessageCountRef.current;
    prevMessageCountRef.current = messages.length;

    if (!hasNewMessages && !isInitial) return;

    // On initial load or when admin just sent a message, always scroll
    if (forceScrollRef.current) {
      forceScrollRef.current = false;
      messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      return;
    }

    if (isInitial) {
      // Initial render: jump without animation so history is visible at bottom
      messagesEndRef.current?.scrollIntoView({ behavior: "auto" as ScrollBehavior });
      return;
    }

    // Subsequent polling updates: only scroll if user is already near bottom
    const container = scrollContainerRef.current;
    if (container) {
      const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight;
      const isNearBottom = distanceFromBottom < 120;
      if (isNearBottom) {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
      }
      // else: user is reading history — leave position alone
    } else {
      // Fallback if ref not attached yet (should not happen)
      if (hasNewMessages && prevCount > 0) {
        // Check via anchor position as fallback — do nothing to avoid yank
      }
    }
  }, [messages]);

  // Detect if human is currently active by scanning ALL messages for the
  // most recent ``human_joined`` / ``human_left`` event. Using the last
  // message alone is unreliable because admin replies land *after* the
  // join event and push it out of the tail position.
  useEffect(() => {
    if (!messages || messages.length === 0) return;
    let lastJoined = -1;
    let lastLeft = -1;
    for (let i = 0; i < messages.length; i++) {
      const m = messages[i];
      const md = (m as { metadata?: Record<string, unknown> }).metadata;
      if (md && typeof md === "object") {
        if ((md as Record<string, unknown>).human_joined) lastJoined = i;
        if ((md as Record<string, unknown>).human_left) lastLeft = i;
      }
    }
    if (lastJoined > lastLeft) {
      setHumanActive(true);
    } else if (lastLeft > lastJoined) {
      setHumanActive(false);
    }
  }, [messages]);

  const handleTakeover = async () => {
    if (!orgId) {
      toast.error("Organization ID not found");
      return;
    }
    setActionLoading(true);
    try {
      const result = await takeoverConversation(orgId, sessionId);
      if (result.success) {
        setHumanActive(true);
        toast.success(result.message);
        await refetch();
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to take over conversation");
    } finally {
      setActionLoading(false);
    }
  };

  const handleRelease = async () => {
    if (!orgId) {
      toast.error("Organization ID not found");
      return;
    }
    setActionLoading(true);
    try {
      const result = await releaseConversation(orgId, sessionId);
      if (result.success) {
        setHumanActive(false);
        toast.success(result.message);
        await refetch();
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to release conversation");
    } finally {
      setActionLoading(false);
    }
  };

  const handleSendMessage = async () => {
    const content = pendingMessage.trim();
    if (!content || !humanActive || !orgId) return;

    setSendingMessage(true);
    try {
      await api.post(`/organizations/${orgId}/conversations/${sessionId}/send-human-message`, {
        content,
      });
      setPendingMessage("");
      toast.success("Message sent");
      forceScrollRef.current = true;
      await refetch();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to send message");
    } finally {
      setSendingMessage(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
        >
          <ArrowLeft className="size-4" /> Back to conversations
        </button>

        {/* Human Control Buttons */}
        <div className="flex items-center gap-2">
          {!humanActive ? (
            <button
              onClick={handleTakeover}
              disabled={actionLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Headset className="size-4" />
              {actionLoading ? "Connecting..." : "Take Control"}
            </button>
          ) : (
            <button
              onClick={handleRelease}
              disabled={actionLoading}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-green-500 text-white rounded-lg hover:bg-green-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <BotIcon className="size-4" />
              {actionLoading ? "Releasing..." : "Release to AI"}
            </button>
          )}
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl p-4 sm:p-4">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-base sm:text-lg font-semibold">Chat Transcript</h2>
            <p className="text-xs text-muted-foreground font-mono">{sessionId}</p>
          </div>
          {humanActive && (
            <span className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-full bg-blue-500/10 text-blue-500">
              <Headset className="size-3" />
              You are controlling this chat
            </span>
          )}
        </div>

        {loading ? (
          <div className="space-y-4 max-h-[60vh] overflow-hidden pr-2 animate-pulse mt-2">
            <div className="flex gap-3 justify-end">
              <div className="h-10 w-2/3 bg-primary/20 rounded-xl rounded-br-sm" />
              <div className="size-7 rounded-full bg-muted shrink-0" />
            </div>
            <div className="flex gap-3 justify-start">
              <div className="size-7 rounded-full bg-primary/10 shrink-0" />
              <div className="h-16 w-3/4 bg-secondary rounded-xl rounded-bl-sm" />
            </div>
            <div className="flex gap-3 justify-start">
              <div className="size-7 rounded-full bg-primary/10 shrink-0" />
              <div className="h-10 w-1/2 bg-secondary rounded-xl rounded-bl-sm" />
            </div>
            <div className="flex gap-3 justify-end">
              <div className="h-10 w-1/3 bg-primary/20 rounded-xl rounded-br-sm" />
              <div className="size-7 rounded-full bg-muted shrink-0" />
            </div>
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 text-red-500 py-6">
            <AlertCircle className="size-4" /> {error}
          </div>
        ) : !messages || messages.length === 0 ? (
          <p className="text-muted-foreground text-sm py-8 text-center">
            No messages recorded for this session yet.
          </p>
        ) : (
          <>
            <style>{`@keyframes highlight { 0% { background-color: hsl(var(--primary) / 0.25); transform: scale(1.01); } 50% { background-color: hsl(var(--primary) / 0.15); } 100% { background-color: transparent; transform: scale(1); } }`}</style>
            <div ref={scrollContainerRef} className="space-y-3 max-h-[60vh] overflow-y-auto pr-2">
            {messages.map((m) => {
               const isHumanMessage = m.sender_type === "human";
               const isSystemMessage = m.sender_type === "system" || (m.metadata && typeof m.metadata === "object" && ("human_joined" in m.metadata || "human_left" in m.metadata));
               const isHighlighted = highlightedMessageId === m.id;
               const delivery = m.role === "agent" ? deliveryLabel(m.send_status) : null;

              // System notification messages (human joined/left)
              if (isSystemMessage) {
                return (
                  <div
                    key={m.id}
                    data-message-id={m.id}
                    className={`flex justify-center transition-all ${isHighlighted ? "ring-2 ring-primary/40 rounded-full animate-pulse" : ""}`}
                  >
                    <span className="text-xs text-muted-foreground bg-secondary/50 px-3 py-1.5 rounded-full">
                      {resolveTranscriptText(m.content) || "—"}
                    </span>
                  </div>
                );
              }

              // Human agent message - different styling
              if (isHumanMessage) {
                return (
                  <div
                    key={m.id}
                    data-message-id={m.id}
                    className={`flex gap-3 justify-start transition-all ${isHighlighted ? "ring-2 ring-blue-500/40 rounded-xl bg-blue-500/20 animate-pulse p-1" : ""}`}
                  >
                    <div className="flex-shrink-0 mt-1">
                      <div className="size-7 rounded-full bg-blue-500 flex items-center justify-center">
                        <Headset className="size-3.5 text-white" />
                      </div>
                    </div>
                    <div className="max-w-[75%] rounded-xl px-4 py-2.5 text-sm leading-relaxed bg-blue-500/10 text-foreground rounded-bl-sm border border-blue-500/20">
                      <p className="whitespace-pre-wrap">
                        {resolveTranscriptText(m.content) || "—"}
                      </p>
                     <span className="block text-[10px] mt-1 opacity-60">
                       {formatTime(m.created_at)}
                       {delivery && (
                         <span className={`ml-2 font-medium ${deliveryBadgeClass(m.send_status)}`}>
                           {delivery}
                         </span>
                       )}
                     </span>
                    </div>
                  </div>
                );
              }

              // Regular user or AI message
              return (
                <div
                  key={m.id}
                  data-message-id={m.id}
                  className={`flex gap-3 ${m.role === "user" ? "justify-end" : "justify-start"} transition-all ${
                    isHighlighted ? "ring-2 ring-primary/40 rounded-xl bg-primary/10 animate-pulse p-1" : ""
                  }`}
                >
                  {m.role === "agent" && (
                    <div className="flex-shrink-0 mt-1">
                      <div className="size-7 rounded-full bg-primary/10 flex items-center justify-center">
                        <Bot className="size-3.5 text-primary" />
                      </div>
                    </div>
                  )}
                  <div
                    className={`max-w-[75%] rounded-xl px-4 py-2.5 text-sm leading-relaxed ${
                      m.role === "user"
                        ? "bg-primary text-primary-foreground rounded-br-sm"
                        : "bg-secondary text-secondary-foreground rounded-bl-sm"
                    }`}
                  >
                    <p className="whitespace-pre-wrap">
                      {resolveTranscriptText(m.content) || "—"}
                    </p>
                     <span className="block text-[10px] mt-1 opacity-60">
                       {formatTime(m.created_at)}
                       {delivery && (
                         <span className={`ml-2 font-medium ${deliveryBadgeClass(m.send_status)}`}>
                           {delivery}
                         </span>
                       )}
                     </span>
                   </div>
                   {m.role === "user" && (
                    <div className="flex-shrink-0 mt-1">
                      <div className="size-7 rounded-full bg-muted flex items-center justify-center">
                        <User className="size-3.5 text-muted-foreground" />
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
            <div ref={messagesEndRef} />
            </div>
          </>
        )}

        {/* Human Message Input (only visible when human is active) */}
        {humanActive && (
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={pendingMessage}
                onChange={(e) => setPendingMessage(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleSendMessage();
                  }
                }}
                placeholder="Type a message as support agent..."
                disabled={sendingMessage}
                className="flex-1 px-4 py-2.5 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
              />
              <button
                onClick={handleSendMessage}
                disabled={!pendingMessage.trim() || sendingMessage}
                className="flex items-center justify-center size-10 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {sendingMessage ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Send className="size-4" />
                )}
              </button>
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              Your messages will be sent to the user. AI is paused while you control this chat.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main Page ────────────────────────────────────
export default function ConversationsPage() {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [statusFilters, setStatusFilters] = useState<string[]>([]);
  const [channelFilters, setChannelFilters] = useState<string[]>([]);
  const [fromDate, setFromDate] = useState("");
  const [toDate, setToDate] = useState("");
  const [sortBy, setSortBy] = useState("updated_at");
  const [sortOrder, setSortOrder] = useState<"asc" | "desc">("desc");
  const [unitFilter, setUnitFilter] = useState<string>("");
  const [providerFilter, setProviderFilter] = useState<string>("");
  const [filterUnits, setFilterUnits] = useState<{ id: string; name: string }[]>([]);
  const [filterProviders, setFilterProviders] = useState<{ id: string; full_name: string; units: string[] }[]>([]);

  useEffect(() => {
    import("@/lib/org-api").then(({ orgApi }) =>
      Promise.all([orgApi.units().catch(() => []), orgApi.providers().catch(() => [])]).then(([u, p]) => {
        setFilterUnits((u as { id: string; name: string }[]).map((x) => ({ id: x.id, name: x.name })));
        setFilterProviders(
          (p as { id: string; full_name: string; units: string[] }[]).map((x) => ({ id: x.id, full_name: x.full_name, units: x.units || [] }))
        );
      })
    ).catch(() => {});
  }, []);

  // Doctor list follows the selected department (same pattern as Bookings).
  const visibleProviders = unitFilter ? filterProviders.filter((p) => p.units.includes(unitFilter)) : filterProviders;
  useEffect(() => {
    if (unitFilter && providerFilter && !visibleProviders.some((p) => p.id === providerFilter)) {
      setProviderFilter("");
      setPage(1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unitFilter]);

  const { data, loading, error } = useConversations(page, 10, {
    search,
    status: statusFilters,
    channel: channelFilters,
    fromDate,
    toDate,
    sortBy,
    sortOrder,
    unit_id: unitFilter || undefined,
    provider_id: providerFilter || undefined,
  });
  const sessions = useMemo(() => data?.items ?? [], [data?.items]);
  const total = data?.total ?? 0;
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const searchParams = useSearchParams();
  const highlightId = searchParams.get("highlight");
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  useEffect(() => {
    const h = searchParams.get("highlight");
    if (h) {
      setHighlightedId(h);
      const t = setTimeout(() => setHighlightedId(null), 3500);
      return () => clearTimeout(t);
    }
  }, [searchParams]);

  useEffect(() => {
    if (!highlightedId || !sessions) return;
    const t = setTimeout(() => {
      const el = document.querySelector(`[data-conversation-id="${highlightedId}"]`);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 400);
    return () => clearTimeout(t);
  }, [highlightedId, sessions]);

  useEffect(() => {
    const id = searchParams.get("id");
    if (id) setSelectedId(id);
    // If highlight is a session id and we're on list view, also auto-select it to show transcript
    const h = searchParams.get("highlight");
    if (h && !id) {
      // Check if highlight matches a session on current page - if so, highlight the row (handled above)
      // If highlight looks like a session UUID and not found on this page, try to navigate to it
      // For now, just highlight the row if present
    }
  }, [searchParams]);

  if (selectedId) {
    return <TranscriptPanel sessionId={selectedId} onBack={() => setSelectedId(null)} />;
  }

  const STATUS_OPTIONS = [
    { value: "CONFIRMED", label: "Confirmed" },
    { value: "CANCELLED", label: "Cancelled" },
  ];
  const CHANNEL_OPTIONS = [
    { value: "web", label: "Web" },
    { value: "whatsapp", label: "WhatsApp" },
  ];
  const SORT_DEFAULTS: Record<string, "asc" | "desc"> = {
    updated_at: "desc",
    created_at: "desc",
    session_status: "asc",
    current_intent: "asc",
    channel: "asc",
  };

  const handleSort = (column: string, order: "asc" | "desc") => {
    setSortBy(column);
    setSortOrder(order);
    setPage(1);
  };

  return (
    <>
      <style>{`@keyframes highlight { 0% { background-color: hsl(var(--primary) / 0.25); transform: scale(1.01); } 50% { background-color: hsl(var(--primary) / 0.15); } 100% { background-color: transparent; transform: scale(1); } }`}</style>
      <div className="mb-6">
        <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">Inbox</h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1">
            Click any session to view the full chat transcript.
          </p>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2">
          <SearchInput
            value={search}
            onChange={(v) => {
              setSearch(v);
              setPage(1);
            }}
            placeholder="Search email, phone, name, session ID…"
            className="w-64"
          />
          <CheckboxFilter
            label="Status"
            options={STATUS_OPTIONS}
            selected={statusFilters}
            onChange={(v) => {
              setStatusFilters(v);
              setPage(1);
            }}
          />
          <CheckboxFilter
            label="Channel"
            options={CHANNEL_OPTIONS}
            selected={channelFilters}
            onChange={(v) => {
              setChannelFilters(v);
              setPage(1);
            }}
          />
          <DateFilter
            label="Created"
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={setFromDate}
            onToChange={setToDate}
          />
          {!!filterUnits.length && (
            <select
              value={unitFilter}
              onChange={(e) => { setUnitFilter(e.target.value); setPage(1); }}
              className="h-9 rounded-md border border-border bg-background px-3 text-xs"
            >
              <option value="">All departments</option>
              {filterUnits.map((u) => (
                <option key={u.id} value={u.id}>{u.name}</option>
              ))}
            </select>
          )}
          {!!filterProviders.length && (
            <select
              value={providerFilter}
              onChange={(e) => { setProviderFilter(e.target.value); setPage(1); }}
              className="h-9 rounded-md border border-border bg-background px-3 text-xs"
            >
              <option value="">All members</option>
              {visibleProviders.map((p) => (
                <option key={p.id} value={p.id}>{p.full_name}</option>
              ))}
            </select>
          )}
          <span className="text-sm text-muted-foreground">{total} conversations</span>
        </div>
      </div>

      <div className="bg-card border border-border rounded-xl overflow-hidden">
        {loading ? (
          <div className="w-full animate-pulse">
            <div className="h-11 border-b border-border bg-muted/50 w-full" />
            {[...Array(5)].map((_, i) => (
              <div key={i} className="flex items-center gap-4 px-4 py-4 border-b border-border">
                <div className="h-4 w-20 bg-secondary rounded" />
                <div className="h-5 w-24 bg-secondary rounded-full" />
                <div className="h-4 w-32 flex-1 bg-secondary rounded" />
                <div className="h-5 w-20 bg-secondary rounded" />
                <div className="h-4 w-16 bg-secondary rounded" />
              </div>
            ))}
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 text-red-500 py-8 px-4">
            <AlertCircle className="size-4" /> {error}
          </div>
        ) : !sessions || !Array.isArray(sessions) || sessions.length === 0 ? (
          <p className="text-muted-foreground text-sm py-12 text-center">No conversations yet.</p>
        ) : (
          <div className="w-full overflow-x-auto">
            {/* Mobile card view */}
            <div className="md:hidden divide-y divide-border">
              {sessions.map((s) => {
                const ident = customerIdentity(s);
                const isHighlighted = highlightedId === s.id;
                return (
                <div
                  key={s.id}
                  data-conversation-id={s.id}
                  onClick={() => setSelectedId(s.id)}
                  className={`p-4 hover:bg-secondary/50 transition-colors cursor-pointer space-y-2 ${isHighlighted ? "bg-primary/10 ring-2 ring-primary/30 animate-pulse" : ""}`}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-medium">
                      {ident.title}
                      {ident.subtitle && (
                        <span className="ml-2 font-mono text-muted-foreground text-[11px]">
                          {ident.subtitle}
                        </span>
                      )}
                    </span>
                    <ChannelBadge channel={s.channel} />
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm text-muted-foreground text-[11px]">{formatDate(s.created_at)}</span>
                    <span className={`text-[11px] font-semibold uppercase px-2 py-1 rounded ${statusBadge(s.session_status)}`}>
                      {s.session_status}
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{s.current_intent || "—"}</span>
                    <span className={s.missing_fields.length > 0 ? "text-yellow-500" : "text-green-500"}>
                      {s.missing_fields.length > 0 ? `${s.missing_fields.length} missing` : "Complete"}
                    </span>
                  </div>
                  {(s.unit_name || s.provider_name) && (
                    <div className="flex gap-1">
                      {s.unit_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary">{s.unit_name}</span>}
                      {s.provider_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">{s.provider_name}</span>}
                    </div>
                  )}
                  <div className="text-[11px] text-muted-foreground">
                      Last active {timeAgo(s.updated_at)} · {formatTime(s.updated_at)}
                    </div>
                </div>
                );
              })}
            </div>
            {/* Desktop table view */}
            <table className="hidden md:table w-full border-collapse">
              <thead>
                <tr>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Customer
                  </th>
                  <SortableTh
                    column="channel"
                    sortBy={sortBy}
                    sortOrder={sortOrder}
                    onSort={handleSort}
                    defaultOrder={SORT_DEFAULTS.channel}
                    className="border-b border-border"
                  >
                    Channel
                  </SortableTh>
                  <SortableTh
                    column="current_intent"
                    sortBy={sortBy}
                    sortOrder={sortOrder}
                    onSort={handleSort}
                    defaultOrder={SORT_DEFAULTS.current_intent}
                    className="border-b border-border"
                  >
                    Intent
                  </SortableTh>
                  <SortableTh
                    column="session_status"
                    sortBy={sortBy}
                    sortOrder={sortOrder}
                    onSort={handleSort}
                    defaultOrder={SORT_DEFAULTS.session_status}
                    className="border-b border-border"
                  >
                    Status
                  </SortableTh>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Missing
                  </th>
                  <SortableTh
                    column="created_at"
                    sortBy={sortBy}
                    sortOrder={sortOrder}
                    onSort={handleSort}
                    defaultOrder={SORT_DEFAULTS.created_at}
                    className="border-b border-border"
                  >
                    Created
                  </SortableTh>
                  <SortableTh
                    column="updated_at"
                    sortBy={sortBy}
                    sortOrder={sortOrder}
                    onSort={handleSort}
                    defaultOrder={SORT_DEFAULTS.updated_at}
                    className="border-b border-border"
                  >
                    Last Active
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => {
                  const ident = customerIdentity(s);
                  const isHighlighted = highlightedId === s.id;
                  return (
                  <tr
                    key={s.id}
                    data-conversation-id={s.id}
                    onClick={() => setSelectedId(s.id)}
                    className={`hover:bg-secondary/50 transition-colors cursor-pointer ${isHighlighted ? "bg-primary/10 ring-2 ring-inset ring-primary/30 animate-pulse" : ""}`}
                  >
                    <td className="px-4 py-4 border-b border-border">
                      <span className="text-[13px] font-medium text-foreground">{ident.title}</span>
                      {ident.subtitle && (
                        <span className="block text-[11px] text-muted-foreground">{ident.subtitle}</span>
                      )}
                    </td>
                    <td className="px-4 py-4 border-b border-border">
                      <ChannelBadge channel={s.channel} />
                      {(s.unit_name || s.provider_name) && (
                        <span className="block mt-1 space-x-1">
                          {s.unit_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary">{s.unit_name}</span>}
                          {s.provider_name && <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary/10 text-primary">{s.provider_name}</span>}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-4 text-[13px] border-b border-border">
                      {s.current_intent || "—"}
                    </td>
                    <td className="px-4 py-4 border-b border-border">
                      <span className={`text-[11px] font-semibold uppercase px-2 py-1 rounded ${statusBadge(s.session_status)}`}>
                        {s.session_status}
                      </span>
                    </td>
                    <td className="px-4 py-4 text-[13px] border-b border-border">
                      {s.missing_fields.length > 0 ? (
                        <span className="text-[11px] text-yellow-500">
                          {s.missing_fields.join(", ")}
                        </span>
                      ) : (
                        <span className="text-[11px] text-green-500">Complete</span>
                      )}
                    </td>
                    <td className="px-4 py-4 text-[13px] border-b border-border">
                      {formatDate(s.created_at)}
                    </td>
                    <td className="px-4 py-4 text-[13px] border-b border-border">
                      <span className="text-foreground">{timeAgo(s.updated_at)}</span>
                      <span className="block text-[11px] text-muted-foreground">
                        {formatTime(s.updated_at)}
                      </span>
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {total > 0 && (
        <Pagination page={page} pageSize={10} total={total} onPageChange={setPage} />
      )}
    </>
  );
}
