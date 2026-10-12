"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { orgApi, Member, Invite } from "@/lib/org-api";
import { useAuth } from "@/lib/auth-context";
import { toast } from "sonner";

export default function StaffPage() {
  const { isDoctor } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("staff");
  const [lastLink, setLastLink] = useState("");

  const load = async () => {
    try {
      const [m, iv] = await Promise.all([
        orgApi.members(),
        orgApi.invites(),
      ]);
      setMembers(m);
      setInvites(iv.filter((x) => !x.accepted_at));
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Failed to load");
    }
  };

  useEffect(() => {
    load();
  }, []);

  if (isDoctor) {
    return (
      <div className="border border-border rounded-xl p-8 bg-card text-center">
        <p className="font-semibold">Not available for team logins</p>
        <p className="text-sm text-muted-foreground mt-1">Staff management is done by your organization.</p>
      </div>
    );
  }

  const invite = async (e: React.FormEvent) => {
    e.preventDefault();
    setLastLink("");
    try {
      // Staff-only invite: no member/provider link (member invites live in Departments).
      const out = await orgApi.invite({ email: email.trim(), role_code: role, provider_id: null });
      const link = out.invite_link || out.invite_token || "";
      setEmail("");
      setLastLink(link);
      if (link) {
        await navigator.clipboard.writeText(link).catch(() => {});
      }
      toast.success(
        out.email_sent
          ? `Invite emailed to ${out.email}`
          : "Invite link ready below — share it"
      );
      load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Invite failed");
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Staff & keys</h1>
        <p className="text-sm text-muted-foreground">Give duplicate keys — reception sees bookings, linked members see only their own.</p>
        <p className="text-xs text-muted-foreground mt-1">
          To invite a team member login, go to <Link href="/dashboard/departments" className="text-primary hover:underline font-medium">Departments → Invite</Link>.
        </p>
      </div>
      <form onSubmit={invite} className="flex flex-col sm:flex-row gap-2">
        <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="reception@hospital.com" className="flex-1 min-w-0 px-3 py-2 bg-background border border-border rounded-lg text-sm" />
        <select value={role} onChange={(e) => setRole(e.target.value)} className="px-3 py-2 bg-background border border-border rounded-lg text-sm">
          <option value="manager">Manager</option>
          <option value="staff">Reception / Staff</option>
          <option value="viewer">Viewer</option>
        </select>
        <button className="px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-semibold whitespace-nowrap">Invite</button>
      </form>
      {lastLink && (
        <div className="grid gap-1 bg-card border border-border rounded-lg px-3 py-2 -mt-3">
          <p className="text-[11px] text-muted-foreground">Login link — they click it, set a password, then sign in:</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 truncate text-xs">{lastLink}</code>
            <button
              type="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(lastLink);
                  toast.success("Invite link copied");
                } catch {
                  toast.error("Copy manually");
                }
              }}
              className="text-xs font-semibold text-primary hover:underline shrink-0"
            >
              Copy link
            </button>
          </div>
        </div>
      )}
      <div>
        <h2 className="text-sm font-semibold mb-2">People ({members.length})</h2>
        <div className="grid gap-2">
          {members.map((m) => (
            <div key={m.id} className="border border-border rounded-lg p-3 bg-card text-sm flex justify-between">
              <span>{m.full_name} · {m.email}</span>
              <span className="text-xs text-muted-foreground">{m.roles.join(", ") || "no role"}</span>
            </div>
          ))}
        </div>
      </div>
      {!!invites.length && (
        <div>
          <h2 className="text-sm font-semibold mb-2">Pending passes ({invites.length})</h2>
          <div className="grid gap-2">
            {invites.map((iv) => (
              <div key={iv.id} className="border border-border rounded-lg p-3 bg-card text-sm flex justify-between">
                <span>{iv.email}</span>
                <span className="text-xs text-muted-foreground">{iv.role_code}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
