"use client";

import { useCallback, useEffect, useState } from "react";
import {
  LifeBuoy,
  Send,
  Loader2,
  Inbox,
  CheckCircle2,
  Clock,
  MessageSquare,
  User,
  Building2,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";

type Ticket = {
  id: string;
  org_name: string | null;
  subject: string;
  detail: string;
  priority: string;
  status: string;
  reply: string | null;
  replied_by_email: string | null;
  replied_at: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
};

const fmtDate = (s: string | null) =>
  s ? new Date(s).toLocaleString() : "—";

const STATUS_STYLES: Record<string, string> = {
  open: "bg-amber-100 text-amber-700 border-amber-200",
  in_progress: "bg-sky-100 text-sky-700 border-sky-200",
  resolved: "bg-emerald-100 text-emerald-700 border-emerald-200",
};

const PRIORITY_STYLES: Record<string, string> = {
  low: "text-slate-500",
  normal: "text-slate-600",
  high: "text-orange-600",
  urgent: "text-red-600",
};

export default function SupportPage() {
  const [subject, setSubject] = useState("");
  const [detail, setDetail] = useState("");
  const [priority, setPriority] = useState("normal");
  const [submitting, setSubmitting] = useState(false);
  const [tickets, setTickets] = useState<Ticket[] | null>(null);
  const [loading, setLoading] = useState(true);

  const loadTickets = useCallback(async () => {
    try {
      const res = await api.get<{ items: Ticket[] }>("/support/requests");
      setTickets(res.items);
    } catch (e) {
      setTickets([]);
      if (e instanceof Error && !e.message.includes("Unauthorized")) {
        toast.error(e.message);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTickets();
    const t = setInterval(() => void loadTickets(), 10000);
    return () => clearInterval(t);
  }, [loadTickets]);

  const submitTicket = async () => {
    if (!subject.trim() || !detail.trim()) {
      toast.error("Please add a subject and a description");
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.post<{ id: string; status: string }>("/support/requests", {
        subject: subject.trim(),
        detail: detail.trim(),
        priority,
      });
      toast.success("Support request submitted — we'll get back to you");
      setSubject("");
      setDetail("");
      setPriority("normal");
      void loadTickets();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to submit request");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Support</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Need help with your DotStark AI agent? File a request and our team
          will get back to you here and by email.
        </p>
      </div>

      {/* ── New request ── */}
      <section className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <LifeBuoy className="size-5 text-primary" />
          <h2 className="text-base font-semibold">Contact support</h2>
        </div>

        <div className="mt-4 grid gap-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium">
              Subject <span className="text-destructive">*</span>
            </label>
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              maxLength={255}
              placeholder="e.g. Widget not loading on my website"
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm outline-none ring-ring/30 transition-shadow focus:ring-2"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium">
              Description <span className="text-destructive">*</span>
            </label>
            <textarea
              value={detail}
              onChange={(e) => setDetail(e.target.value)}
              maxLength={5000}
              rows={5}
              placeholder="What's happening? Include steps to reproduce if relevant."
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm outline-none ring-ring/30 transition-shadow focus:ring-2"
            />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <label className="mb-1.5 block text-sm font-medium">Priority</label>
              <select
                value={priority}
                onChange={(e) => setPriority(e.target.value)}
                className="h-10 rounded-md border border-input bg-background px-3 text-sm outline-none"
              >
                <option value="low">Low</option>
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </div>
            <button
              onClick={submitTicket}
              disabled={submitting}
              className="inline-flex h-10 items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
            >
              {submitting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Send className="size-4" />
              )}
              {submitting ? "Submitting…" : "Submit request"}
            </button>
          </div>
        </div>
      </section>

      {/* ── Ticket history ── */}
      <section className="rounded-xl border border-border bg-card p-6 shadow-sm">
        <div className="flex items-center gap-2">
          <Inbox className="size-5 text-primary" />
          <h2 className="text-base font-semibold">Your requests</h2>
          {tickets && tickets.length > 0 && (
            <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
              {tickets.length}
            </span>
          )}
        </div>

        <div className="mt-4 space-y-3">
          {loading && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading your requests…
            </p>
          )}
          {!loading && tickets && tickets.length === 0 && (
            <p className="rounded-md border border-dashed border-border py-8 text-center text-sm text-muted-foreground">
              No support requests yet. Tell us how we can help!
            </p>
          )}
          {!loading &&
            (tickets ?? []).map((t) => (
              <div
                key={t.id}
                className="rounded-lg border border-border bg-background p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-medium">{t.subject}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <Building2 className="size-3" />
                        {t.org_name ?? "—"}
                      </span>
                      <span className="inline-flex items-center gap-1">
                        <Clock className="size-3" />
                        {fmtDate(t.created_at)}
                      </span>
                      <span
                        className={`font-medium ${PRIORITY_STYLES[t.priority] ?? "text-slate-500"}`}
                      >
                        {t.priority.charAt(0).toUpperCase() + t.priority.slice(1)}
                      </span>
                    </p>
                  </div>
                  <span
                    className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${STATUS_STYLES[t.status] ?? "bg-slate-100 text-slate-600 border-slate-200"}`}
                  >
                    {t.status === "in_progress"
                      ? "In progress"
                      : t.status.charAt(0).toUpperCase() + t.status.slice(1)}
                  </span>
                </div>

                <p className="mt-3 whitespace-pre-wrap text-sm text-muted-foreground">
                  {t.detail}
                </p>

                {t.reply && (
                  <div className="mt-3 rounded-md border border-primary/20 bg-primary/5 p-3">
                    <p className="flex items-center gap-1.5 text-xs font-semibold text-primary">
                      <MessageSquare className="size-3.5" />
                      Response from our team
                      {t.replied_at && (
                        <span className="font-normal text-muted-foreground">
                          · {fmtDate(t.replied_at)}
                        </span>
                      )}
                    </p>
                    <p className="mt-1.5 whitespace-pre-wrap text-sm">{t.reply}</p>
                    {t.replied_by_email && (
                      <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
                        <User className="size-3" /> {t.replied_by_email}
                      </p>
                    )}
                  </div>
                )}

                {t.status === "resolved" && t.resolved_at && (
                  <p className="mt-3 flex items-center gap-1.5 text-xs font-medium text-emerald-600">
                    <CheckCircle2 className="size-3.5" />
                    Resolved on {fmtDate(t.resolved_at)}
                  </p>
                )}
              </div>
            ))}
        </div>
      </section>
    </div>
  );
}
