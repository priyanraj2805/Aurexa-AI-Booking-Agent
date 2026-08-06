"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/components/toast";

type Settings = {
  mfa_enforced: boolean;
  password_policy: {
    min_length: number;
    require_upper: boolean;
    require_digit: boolean;
    require_special: boolean;
  };
  ip_allowlist: {
    enabled: boolean;
    cidrs: string[];
  };
};

export default function SettingsPage() {
  const { can } = useAuth();
  const { toast } = useToast();

  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setSettings(await api.get<Settings>("/security/settings"));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load settings");
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const canManage = can("security.manage");

  const update = (patch: Partial<Settings>) => {
    setSettings((s) => (s ? { ...s, ...patch } : s));
  };
  const updatePolicy = (patch: Partial<Settings["password_policy"]>) => {
    setSettings((s) =>
      s ? { ...s, password_policy: { ...s.password_policy, ...patch } } : s
    );
  };
  const updateAllowlist = (patch: Partial<Settings["ip_allowlist"]>) => {
    setSettings((s) =>
      s ? { ...s, ip_allowlist: { ...s.ip_allowlist, ...patch } } : s
    );
  };

  const save = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      await api.put<Settings>("/security/settings", settings);
      toast("success", "Security settings saved");
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  if (error) {
    return <p className="text-sm text-red-400" role="alert">{error}</p>;
  }

  if (!settings) {
    return <p className="text-sm text-slate-400">Loading…</p>;
  }

  const disabled = !canManage;
  const toggle =
    "relative h-6 w-11 rounded-full transition-colors disabled:opacity-40";

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Security settings</h1>
        <p className="mt-1 text-sm text-slate-400">
          Platform-wide policy for all super admins. Applied on every request.
        </p>
      </div>

      <section className="rounded-lg border border-slate-800 p-5">
        <h2 className="text-lg font-semibold">MFA policy</h2>
        <div className="mt-3 flex items-center justify-between">
          <div>
            <p className="text-sm text-slate-200">Require MFA for all admins</p>
            <p className="text-xs text-slate-500">
              When off, admins with MFA configured still use it on login.
            </p>
          </div>
          <button
            onClick={() => update({ mfa_enforced: !settings.mfa_enforced })}
            disabled={disabled}
            className={toggle}
            aria-label="Toggle MFA enforcement"
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${
                settings.mfa_enforced ? "left-[22px]" : "left-0.5"
              }`}
            />
          </button>
        </div>
      </section>

      <section className="rounded-lg border border-slate-800 p-5">
        <h2 className="text-lg font-semibold">Password policy</h2>
        <div className="mt-3 space-y-3 text-sm">
          <div className="flex items-center justify-between">
            <label htmlFor="min-length" className="text-slate-200">
              Minimum length
            </label>
            <input
              id="min-length"
              type="number"
              min={6}
              max={64}
              value={settings.password_policy.min_length}
              onChange={(e) =>
                updatePolicy({ min_length: Number(e.target.value) })
              }
              disabled={disabled}
              className="w-20 rounded-md border border-slate-700 bg-slate-950 px-3 py-1.5 text-center outline-none focus:border-indigo-500 disabled:opacity-40"
            />
          </div>
          {(
            [
              ["require_upper", "Require an uppercase letter"],
              ["require_digit", "Require a digit"],
              ["require_special", "Require a special character"],
            ] as const
          ).map(([key, label]) => (
            <div key={key} className="flex items-center justify-between">
              <span className="text-slate-200">{label}</span>
              <button
                onClick={() =>
                  updatePolicy({ [key]: !settings.password_policy[key] })
                }
                disabled={disabled}
                className={toggle}
                aria-label={`Toggle ${label}`}
              >
                <span
                  className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${
                    settings.password_policy[key] ? "left-[22px]" : "left-0.5"
                  }`}
                />
              </button>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-slate-800 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">IP allowlist</h2>
            <p className="text-xs text-slate-500">
              When enabled, super admins can only sign in from matching CIDRs.
              Enter one per line (e.g. 10.0.0.0/8, 192.168.1.0/24).
            </p>
          </div>
          <button
            onClick={() =>
              updateAllowlist({ enabled: !settings.ip_allowlist.enabled })
            }
            disabled={disabled}
            className={toggle}
            aria-label="Toggle IP allowlist"
          >
            <span
              className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${
                settings.ip_allowlist.enabled ? "left-[22px]" : "left-0.5"
              }`}
            />
          </button>
        </div>
        <textarea
          value={settings.ip_allowlist.cidrs.join("\n")}
          onChange={(e) =>
            updateAllowlist({
              cidrs: e.target.value
                .split("\n")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          disabled={disabled || !settings.ip_allowlist.enabled}
          rows={4}
          placeholder="10.0.0.0/8"
          className="mt-3 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm outline-none focus:border-indigo-500 disabled:opacity-40"
        />
        {settings.ip_allowlist.enabled && (
          <p className="mt-2 rounded-md border border-amber-900 bg-amber-950/30 p-2 text-xs text-amber-300">
            Warning: if your own IP is not in the list, you will be locked out
            immediately on save.
          </p>
        )}
      </section>

      <div className="flex justify-end">
        <button
          onClick={save}
          disabled={disabled || saving}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700 disabled:opacity-40"
        >
          {saving ? "Saving…" : "Save settings"}
        </button>
      </div>
    </div>
  );
}
