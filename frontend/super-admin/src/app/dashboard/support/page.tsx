"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import TicketsPanel from "@/components/support/tickets-panel";
import ImpersonationPanel from "@/components/support/impersonation-panel";

type TabKey = "tickets" | "impersonation";

export default function SupportPage() {
  const { can } = useAuth();
  const canViewTickets = can("support.tickets.view");
  const canImpersonate = can("support.impersonate") || can("support.conversations.view");

  const searchParams = useSearchParams();
  const initialRequestId = searchParams.get("impersonation") ?? undefined;
  const requestNonce = searchParams.get("imp_t") ?? undefined;

  const [tab, setTab] = useState<TabKey>(() =>
    initialRequestId ? "impersonation" : canViewTickets ? "tickets" : "impersonation"
  );

  const tabs: { key: TabKey; label: string; visible: boolean }[] = [
    { key: "tickets", label: "Tickets", visible: canViewTickets },
    { key: "impersonation", label: "Impersonation", visible: canImpersonate },
  ];

  const visibleTabs = tabs.filter((t) => t.visible);
  const activeTab =
    visibleTabs.some((t) => t.key === tab) ? tab : (visibleTabs[0]?.key ?? "tickets");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Support</h1>
        <p className="mt-1 text-sm text-slate-400">
          Triage org-admin support tickets and impersonate users for assisted
          troubleshooting.
        </p>
      </div>

      <div className="flex gap-1.5 border-b border-slate-800 text-sm">
        {visibleTabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`-mb-px rounded-t-md border border-b-0 px-4 py-2 ${
              activeTab === t.key
                ? "border-slate-700 bg-slate-900 text-slate-100"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === "tickets" ? (
        <TicketsPanel />
      ) : (
        <ImpersonationPanel initialRequestId={initialRequestId} requestNonce={requestNonce} />
      )}
    </div>
  );
}
