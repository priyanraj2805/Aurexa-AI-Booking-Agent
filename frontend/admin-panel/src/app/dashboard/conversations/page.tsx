"use client";

import { useState, useRef, useEffect } from "react";
import { Loader2, AlertCircle, ArrowLeft, User, Bot, Globe, MessageCircle, Headset, Bot as BotIcon, Send } from "lucide-react";
import { useConversations, useTranscript, takeoverConversation, releaseConversation, sendHumanMessage } from "@/hooks/use-dashboard";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { toast } from "sonner";

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
  const isWhatsApp = channel === "whatsapp";
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

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
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
  const orgId = user?.org_id ?? "";

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
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
          <div className="space-y-3 max-h-[60vh] overflow-y-auto pr-2">
            {messages.map((m) => {
              const isHumanMessage = m.sender_type === "human";
              const isSystemMessage = m.sender_type === "system" || (m.metadata && typeof m.metadata === "object" && ("human_joined" in m.metadata || "human_left" in m.metadata));

              // System notification messages (human joined/left)
              if (isSystemMessage) {
                return (
                  <div key={m.id} className="flex justify-center">
                    <span className="text-xs text-muted-foreground bg-secondary/50 px-3 py-1.5 rounded-full">
                      {resolveTranscriptText(m.content) || "—"}
                    </span>
                  </div>
                );
              }

              // Human agent message - different styling
              if (isHumanMessage) {
                return (
                  <div key={m.id} className="flex gap-3 justify-start">
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
                      </span>
                    </div>
                  </div>
                );
              }

              // Regular user or AI message
              return (
                <div
                  key={m.id}
                  className={`flex gap-3 ${m.role === "user" ? "justify-end" : "justify-start"}`}
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
  const { data: sessions, loading, error } = useConversations(200);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  if (selectedId) {
    return <TranscriptPanel sessionId={selectedId} onBack={() => setSelectedId(null)} />;
  }

  return (
    <>
      <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">Inbox</h1>
        <p className="text-muted-foreground text-xs sm:text-sm mt-1">
          Click any session to view the full chat transcript.
        </p>
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
              {sessions.map((s) => (
                <div
                  key={s.id}
                  onClick={() => setSelectedId(s.id)}
                  className="p-4 hover:bg-secondary/50 transition-colors cursor-pointer space-y-2"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[13px] font-mono text-muted-foreground">{s.id.slice(0, 8)}…</span>
                    <ChannelBadge channel={s.channel} />
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{s.current_intent || "—"}</span>
                    <span className={`text-[11px] font-semibold uppercase px-2 py-1 rounded ${statusBadge(s.session_status)}`}>
                      {s.session_status}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="text-muted-foreground">
                      {Object.keys(s.collected_data).length > 0
                        ? Object.keys(s.collected_data).join(", ")
                        : "—"}
                    </span>
                    <span className={s.missing_fields.length > 0 ? "text-yellow-500" : "text-green-500"}>
                      {s.missing_fields.length > 0 ? `${s.missing_fields.length} missing` : "Complete"}
                    </span>
                  </div>
                  <div className="text-[11px] text-muted-foreground">{timeAgo(s.updated_at)}</div>
                </div>
              ))}
            </div>
            {/* Desktop table view */}
            <table className="hidden md:table w-full border-collapse">
              <thead>
                <tr>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Session ID
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Channel
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Intent
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Collected
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Missing
                  </th>
                  <th className="px-4 py-3 text-left text-[11px] font-semibold text-muted-foreground uppercase border-b border-border">
                    Last Activity
                  </th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr
                    key={s.id}
                    onClick={() => setSelectedId(s.id)}
                    className="hover:bg-secondary/50 transition-colors cursor-pointer"
                  >
                    <td className="px-4 py-4 text-[13px] border-b border-border font-mono text-muted-foreground">
                      {s.id.slice(0, 8)}…
                    </td>
                    <td className="px-4 py-4 border-b border-border">
                      <ChannelBadge channel={s.channel} />
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
                      {Object.keys(s.collected_data).length > 0 ? (
                        <span className="text-[11px] text-muted-foreground">
                          {Object.keys(s.collected_data).join(", ")}
                        </span>
                      ) : (
                        "—"
                      )}
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
                      {timeAgo(s.updated_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
