"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";
import { ConfirmDialog } from "@/components/modal";

type UserProfile = {
  id: string;
  org_id: string;
  org_name: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  created_at: string | null;
  booking_count: number;
  conversation_count: number;
  last_activity_at: string | null;
  recent_bookings: {
    id: string;
    customer_name: string;
    customer_email: string;
    org_slot_start_at: string | null;
    meeting_type_snapshot: string | null;
    created_at: string | null;
  }[];
};

type ActivityRow = {
  id: string;
  actor_email: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  created_at: string | null;
};

export default function UserDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { can } = useAuth();
  const { toast } = useToast();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [activity, setActivity] = useState<ActivityRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"disable" | "enable" | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [u, a] = await Promise.all([
        api.get<UserProfile>(`/tenants/users/${id}`),
        api.get<{ items: ActivityRow[] }>(`/tenants/users/${id}/activity`),
      ]);
      setUser(u);
      setActivity(a.items);
    } catch (e) {
      if (e instanceof Error && e.message === "Unauthorized") {
        router.replace("/login");
        return;
      }
      setError(e instanceof Error ? e.message : "Failed to load user");
    }
  }, [id, router]);

  useEffect(() => {
    load();
  }, [load]);

  const runToggle = async () => {
    if (!user || !confirm) return;
    const enable = confirm === "enable";
    setBusy(true);
    try {
      await api.post(`/lifecycle/users/${user.id}/${enable ? "enable" : "disable"}`);
      toast("success", `${user.email} ${enable ? "enabled" : "disabled"}`);
      setConfirm(null);
      await load();
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  };

  if (error) {
    return (
      <div>
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
        <Link
          href="/dashboard/users"
          className="text-sm text-indigo-400 hover:underline"
        >
          ← Back to users
        </Link>
      </div>
    );
  }

  if (!user) return <p className="text-slate-400">Loading…</p>;

  return (
    <div>
      <Link
        href="/dashboard/users"
        className="text-sm text-indigo-400 hover:underline"
      >
        ← Back to users
      </Link>

      <div className="mt-2 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{user.full_name ?? user.email}</h1>
          <p className="text-sm text-slate-400">
            {user.email} ·{" "}
            <Link
              href={`/dashboard/organizations/${user.org_id}`}
              className="text-indigo-400 hover:underline"
            >
              {user.org_name}
            </Link>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`rounded-full px-3 py-1 text-xs font-semibold ${
              user.is_active
                ? "bg-green-900/60 text-green-300"
                : "bg-red-900/60 text-red-300"
            }`}
          >
            {user.is_active ? "ACTIVE" : "DISABLED"}
          </span>
          {user.is_active && can("users.disable") && (
            <button
              onClick={() => setConfirm("disable")}
              className="rounded-md border border-amber-700 px-3 py-1.5 text-sm text-amber-300 hover:bg-amber-950"
            >
              Disable
            </button>
          )}
          {!user.is_active && can("users.enable") && (
            <button
              onClick={() => setConfirm("enable")}
              className="rounded-md border border-green-700 px-3 py-1.5 text-sm text-green-300 hover:bg-green-950"
            >
              Enable
            </button>
          )}
        </div>
      </div>

      {confirm && (
        <ConfirmDialog
          title={confirm === "disable" ? "Disable user" : "Enable user"}
          message={
            confirm === "disable" ? (
              <>
                Block <b>{user.email}</b> from logging in. The user can be re-enabled
                at any time.
              </>
            ) : (
              <>
                Restore login access for <b>{user.email}</b>.
              </>
            )
          }
          confirmLabel={confirm === "disable" ? "Disable" : "Enable"}
          danger={confirm === "disable"}
          busy={busy}
          onConfirm={runToggle}
          onCancel={() => setConfirm(null)}
        />
      )}

      <div className="mt-6 grid grid-cols-4 gap-4">
        {[
          ["Bookings", user.booking_count],
          ["Conversations", user.conversation_count],
          [
            "Joined",
            user.created_at ? new Date(user.created_at).toLocaleDateString() : "—",
          ],
          [
            "Last activity",
            user.last_activity_at
              ? new Date(user.last_activity_at).toLocaleDateString()
              : "—",
          ],
        ].map(([label, value]) => (
          <div
            key={label as string}
            className="rounded-lg border border-slate-800 bg-slate-900/50 p-4"
          >
            <p className="text-xs uppercase tracking-wide text-slate-400">
              {label}
            </p>
            <p className="mt-1 text-xl font-semibold">{value}</p>
          </div>
        ))}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Recent bookings
          </h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-2 py-2">Customer</th>
                  <th className="px-2 py-2">Slot</th>
                  <th className="px-2 py-2">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {user.recent_bookings.map((b) => (
                  <tr key={b.id}>
                    <td className="px-2 py-2">{b.customer_name}</td>
                    <td className="px-2 py-2 text-slate-300">
                      {b.org_slot_start_at
                        ? new Date(b.org_slot_start_at).toLocaleString()
                        : "—"}
                    </td>
                    <td className="px-2 py-2 text-slate-400">
                      {b.created_at
                        ? new Date(b.created_at).toLocaleString()
                        : "—"}
                    </td>
                  </tr>
                ))}
                {user.recent_bookings.length === 0 && (
                  <tr>
                    <td colSpan={3} className="px-2 py-6 text-center text-slate-500">
                      No bookings by this user
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">
            Control-plane activity
          </h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs uppercase tracking-wide text-slate-400">
                <tr>
                  <th className="px-2 py-2">Time</th>
                  <th className="px-2 py-2">Actor</th>
                  <th className="px-2 py-2">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800">
                {activity.map((a) => (
                  <tr key={a.id}>
                    <td className="px-2 py-2 text-slate-400">
                      {a.created_at ? new Date(a.created_at).toLocaleString() : "—"}
                    </td>
                    <td className="px-2 py-2">{a.actor_email ?? "—"}</td>
                    <td className="px-2 py-2">
                      <span className="rounded bg-slate-800 px-2 py-0.5 font-mono text-xs">
                        {a.action}
                      </span>
                    </td>
                  </tr>
                ))}
                {activity.length === 0 && (
                  <tr>
                    <td colSpan={3} className="px-2 py-6 text-center text-slate-500">
                      No control-plane actions for this user
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
