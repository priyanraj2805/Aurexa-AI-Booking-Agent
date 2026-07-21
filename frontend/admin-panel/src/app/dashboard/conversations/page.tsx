"use client";

import { useState } from "react";
import { Loader2, AlertCircle, ArrowLeft, User, Bot, Globe, MessageCircle } from "lucide-react";
import { useConversations, useTranscript } from "@/hooks/use-dashboard";

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
  const { data: messages, loading, error } = useTranscript(sessionId);

  return (
    <div className="space-y-4">
      <button
        onClick={onBack}
        className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        <ArrowLeft className="size-4" /> Back to conversations
      </button>

      <div className="bg-card border border-border rounded-xl p-4">
        <h2 className="text-lg font-semibold mb-1">Chat Transcript</h2>
        <p className="text-xs text-muted-foreground mb-4 font-mono">{sessionId}</p>

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
            {messages.map((m) => (
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
            ))}
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
          <h1 className="text-[28px] font-semibold tracking-tight leading-none">Inbox</h1>
        <p className="text-muted-foreground text-sm mt-1">
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
            <table className="w-full border-collapse">
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
