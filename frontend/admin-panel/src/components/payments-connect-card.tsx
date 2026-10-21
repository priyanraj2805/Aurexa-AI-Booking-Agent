"use client";

import { useState, useEffect } from "react";
import { Loader2, PlugZap, Unplug, BadgeCheck, Landmark } from "lucide-react";
import { toast } from "sonner";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

interface PaymentsConfig {
  provider: string;
  cashfree?: {
    connected: boolean;
    mode: string;
    app_id_masked: string;
  };
  webhook_url?: string;
}

/** Cashfree own-account connect card â€” money settles straight to your bank. */
export function PaymentsConnectCard() {
  const { user } = useAuth();
  // Read-only: environment is platform-controlled (server CASHFREE_MODE).
  // Tenants never choose test/live in the dashboard.
  const [payMode, setPayMode] = useState<string>("test");
  const [payAppId, setPayAppId] = useState("");
  const [paySecret, setPaySecret] = useState("");
  const [payConnected, setPayConnected] = useState(false);
  const [payMasked, setPayMasked] = useState("");
  const [payWebhook, setPayWebhook] = useState("");
  const [payLoading, setPayLoading] = useState(true);
  const [paySaving, setPaySaving] = useState(false);
  const [payTesting, setPayTesting] = useState(false);
  const [showReplace, setShowReplace] = useState(false);

  // Appointment price â€” the chat widget only asks for payment when a price
  // is enabled. Same setting the Route card uses; kept here so Cashfree
  // tenants don't have to hunt through the Razorpay section.
  const [priceEnabled, setPriceEnabled] = useState(false);
  const [priceRupees, setPriceRupees] = useState("500");
  const [priceSaving, setPriceSaving] = useState(false);

  const loadStatus = async () => {
    try {
      const cfg = await api.get<PaymentsConfig>("/dashboard/payments/config");
      setPayMode(cfg.cashfree?.mode ?? "test");
      setPayConnected(!!cfg.cashfree?.connected);
      setPayMasked(cfg.cashfree?.app_id_masked ?? "");
      setPayWebhook(cfg.webhook_url ?? "");
    } catch {
      /* stays disconnected on error */
    }
    try {
      const route = await api.get<{ price_enabled: boolean; price_paise: number }>(
        "/dashboard/razorpay/route/status"
      );
      setPriceEnabled(!!route.price_enabled);
      if (route.price_paise) setPriceRupees(String(route.price_paise / 100));
    } catch {
      /* route status unavailable â€” price stays off by default */
    }
  };

  useEffect(() => {
    (async () => {
      await loadStatus();
      setPayLoading(false);
    })();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!payConnected && (!payAppId.trim() || !paySecret.trim())) {
      toast.error("Cashfree App ID and Secret Key are required");
      return;
    }
    setPaySaving(true);
    try {
      const body: Record<string, string> = { provider: "cashfree" };
      if (payAppId.trim()) body.cashfree_app_id = payAppId.trim();
      if (paySecret.trim()) body.cashfree_secret_key = paySecret.trim();
      const cfg = await api.put<PaymentsConfig>("/dashboard/payments/config", body);
      setPayMode(cfg.cashfree?.mode ?? payMode);
      setPayConnected(!!cfg.cashfree?.connected);
      setPayMasked(cfg.cashfree?.app_id_masked ?? "");
      setPayWebhook(cfg.webhook_url ?? payWebhook);
      setPaySecret("");
      setPayAppId("");
      setShowReplace(false);
      toast.success("Cashfree connected");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save Cashfree keys");
    } finally {
      setPaySaving(false);
    }
  };

  const handleTest = async () => {
    setPayTesting(true);
    try {
      const res = await api.post<{ message?: string }>("/dashboard/payments/cashfree/test", {});
      toast.success(res.message || "Cashfree keys verified successfully");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Connection test failed");
    } finally {
      setPayTesting(false);
    }
  };

  const handleDisconnect = async () => {
    setPaySaving(true);
    try {
      await api.del("/dashboard/payments/cashfree");
      setPayConnected(false);
      setPayMasked("");
      setPayAppId("");
      setPaySecret("");
      toast.success("Cashfree disconnected");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not disconnect");
    } finally {
      setPaySaving(false);
    }
  };

  const handleTogglePrice = async (next: boolean) => {
    setPriceEnabled(next);
    setPriceSaving(true);
    try {
      // No amount box here on purpose: the charge follows the department /
      // team-member fee from the catalog. Echo back the stored fallback
      // price so legacy flat-price orgs keep working.
      const paise = Math.round(parseFloat(priceRupees || "0") * 100);
      await api.put("/dashboard/razorpay/price", {
        enabled: next,
        amount_paise: next ? paise : 0,
        currency: "INR",
      });
      toast.success(next ? "Paid appointments enabled" : "Paid appointments disabled");
    } catch (err) {
      setPriceEnabled(!next);
      toast.error(err instanceof Error ? err.message : "Could not save setting");
    } finally {
      setPriceSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Landmark className="size-5 text-primary" />
              Cashfree â€” your own account
            </CardTitle>
            <CardDescription className="mt-1">
              Booking payments settle straight into your bank account. Keys stay encrypted on our server.
            </CardDescription>
          </div>
          {payConnected ? (
            <span className="shrink-0 rounded-full bg-green-500/15 px-3 py-1 text-xs font-semibold text-green-600">
              <BadgeCheck className="mr-1 inline size-3.5" />
              Connected{payMasked ? ` ${payMasked}` : ""}
            </span>
          ) : (
            <span className="shrink-0 rounded-full bg-zinc-500/15 px-3 py-1 text-xs font-semibold text-zinc-500">
              Not connected
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {payLoading ? (
          <p className="text-sm text-muted-foreground flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" /> Loadingâ€¦
          </p>
        ) : (
          <form onSubmit={handleSave} className="space-y-4">
            {payConnected && !showReplace ? (
              <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold font-mono">{payMasked || "Connected"}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">
                      {payMode === "live" ? "Live mode â€” real money" : "Test mode â€” sandbox"} â€¢ payments settle straight into your bank
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button type="button" variant="outline" size="sm" onClick={() => setShowReplace(true)} className="h-7 text-xs">
                      Replace keys
                    </Button>
                    <Button type="button" variant="ghost" size="sm" onClick={handleDisconnect} disabled={paySaving} className="h-7 text-xs text-red-500">
                      <Unplug className="size-3" /> Disconnect
                    </Button>
                  </div>
                </div>
              </div>
            ) : (
              <>
                <div className="grid gap-4 sm:grid-cols-2">
                  <div>
                    <Label htmlFor="cfAppId">Cashfree App ID</Label>
                    <Input
                      id="cfAppId"
                      value={payAppId}
                      onChange={(e) => setPayAppId(e.target.value)}
                      placeholder={payConnected ? "Saved â€” enter new to replace" : "TEST1129â€¦"}
                      className="mt-1 font-mono text-xs"
                      autoComplete="off"
                    />
                  </div>
                  <div>
                    <Label htmlFor="cfSecret">Cashfree Secret Key</Label>
                    <Input
                      id="cfSecret"
                      type="password"
                      value={paySecret}
                      onChange={(e) => setPaySecret(e.target.value)}
                      placeholder={payConnected ? "Saved â€” enter new to replace" : "cfsk_â€¦"}
                      className="mt-1 font-mono text-xs"
                      autoComplete="new-password"
                    />
                    <p className="mt-1 text-[11px] text-muted-foreground">
                      Stored encrypted. Never shown again after saving.
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Button type="submit" size="sm" disabled={paySaving} className="h-8 text-xs">
                    {paySaving && <Loader2 className="size-3 animate-spin" />}
                    {payConnected ? "Update keys" : "Connect Cashfree"}
                  </Button>
                  {payConnected && (
                    <>
                      <Button type="button" variant="ghost" size="sm" onClick={() => { setShowReplace(false); setPayAppId(""); setPaySecret(""); }} className="h-8 text-xs">
                        Cancel
                      </Button>
                      <Button type="button" variant="ghost" size="sm" onClick={handleDisconnect} disabled={paySaving} className="h-8 text-xs text-red-500">
                        <Unplug className="size-3" /> Disconnect
                      </Button>
                    </>
                  )}
                </div>

                {/* Webhook URL for the tenant's Cashfree dashboard (optional) */}
                {payWebhook ? (
                  <div className="rounded-lg bg-muted/50 p-3">
                    <p className="text-xs font-semibold">Cashfree webhook URL</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Paste this URL in your Cashfree dashboard â†’ Developers â†’ Webhooks. (Optional â€” it is sent automatically with every order.)
                    </p>
                    <div className="mt-2 flex gap-2">
                      <Input value={payWebhook} readOnly className="font-mono text-xs flex-1" />
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="h-8 text-xs shrink-0"
                        onClick={async () => {
                          try {
                            await navigator.clipboard.writeText(payWebhook);
                            toast.success("Webhook URL copied");
                          } catch {
                            toast.error("Copy failed â€” please copy it manually");
                          }
                        }}
                      >
                        Copy
                      </Button>
                    </div>
                  </div>
                ) : null}

              </>
            )}

            {/* Appointment price switch */}
            <div className="rounded-lg border p-4 space-y-2">
              <p className="text-xs font-semibold">Appointment price</p>
              <p className="text-xs text-muted-foreground">The chat widget shows the Pay button only when this is on. The amount charged comes from your department / team-member fee.</p>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={priceEnabled} disabled={priceSaving} onChange={(e) => handleTogglePrice(e.target.checked)} />
                Enable paid appointments
                {priceSaving && <Loader2 className="size-3 animate-spin" />}
              </label>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
