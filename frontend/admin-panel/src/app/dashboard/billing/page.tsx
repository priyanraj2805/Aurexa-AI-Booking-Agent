"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, CreditCard, Sparkles, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

interface LimitKey {
  key: string;
  label: string;
  unit?: string;
}

const LIMIT_LABELS: LimitKey[] = [
  { key: "bookings_per_month", label: "Bookings", unit: "/month" },
  { key: "ai_tokens_per_month", label: "AI tokens", unit: "/month" },
  { key: "seats", label: "Team seats" },
  { key: "storage_gb", label: "Storage", unit: "GB" },
];

interface Plan {
  id: string;
  code: string;
  name: string;
  description: string | null;
  monthly_price_cents: number;
  period: string;
  limits: Record<string, number | null>;
}

interface PlanInfo {
  plan: Plan | null;
  subscription: {
    id: string;
    status: string;
    cancel_at_period_end: boolean;
    current_period_end: string | null;
  } | null;
  usage: Record<string, number>;
  catalog: Plan[];
}

function formatPrice(cents: number): string {
  if (cents === 0) return "$0";
  return `$${(cents / 100).toFixed(2)}`;
}

function formatLimitValue(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Unlimited";
  return String(value);
}

export default function BillingPage() {
  const { user } = useAuth();
  const [data, setData] = useState<PlanInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedPlan, setSelectedPlan] = useState<Plan | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [changing, setChanging] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get<PlanInfo>("/account/plan");
      setData(res);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not load plan information");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const currentPlanId = data?.plan?.id ?? null;

  const loadRazorpayScript = (): Promise<boolean> => {
    if (typeof window === "undefined") return Promise.resolve(false);
    if ((window as unknown as { Razorpay?: unknown }).Razorpay) return Promise.resolve(true);
    return new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = "https://checkout.razorpay.com/v1/checkout.js";
      script.onload = () => resolve(true);
      script.onerror = () => resolve(false);
      document.body.appendChild(script);
    });
  };

  const handleChange = async () => {
    if (!selectedPlan) return;
    // Free plan — no payment needed
    if (selectedPlan.monthly_price_cents === 0) {
      setChanging(true);
      try {
        await api.post("/account/plan", { plan_id: selectedPlan.id });
        toast.success(`Your plan is now ${selectedPlan.name}`);
        setSelectedPlan(null);
        await load();
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Plan change failed");
      } finally {
        setChanging(false);
        setConfirming(false);
      }
      return;
    }

    // Paid plan — Razorpay flow
    setChanging(true);
    try {
      const order = await api.post<{ order_id: string; amount: number; currency: string; key_id: string; plan_code: string }>(
        "/account/razorpay/order",
        { plan_id: selectedPlan.id },
      );

      // If backend returned a free order (amount 0 or no key), just verify directly
      if (!order.key_id || order.amount === 0) {
        await api.post("/account/razorpay/verify", {
          plan_id: selectedPlan.id,
          razorpay_order_id: order.order_id,
          razorpay_payment_id: `pay_free_${Date.now()}`,
          razorpay_signature: "free",
        });
        toast.success(`Payment verified — your plan is now ${selectedPlan.name}`);
        setSelectedPlan(null);
        await load();
        return;
      }

      const loaded = await loadRazorpayScript();
      if (!loaded) {
        toast.error("Failed to load Razorpay. Please try again.");
        return;
      }

      const Razorpay = (window as unknown as { Razorpay: new (opts: unknown) => { open: () => void } }).Razorpay;
      const options = {
        key: order.key_id,
        amount: order.amount,
        currency: order.currency || "INR",
        name: "DotStark",
        description: `${selectedPlan.name} — ${selectedPlan.code}`,
        order_id: order.order_id,
        prefill: {
          name: user?.full_name || "",
          email: user?.email || "",
        },
        theme: { color: "#6366f1" },
        handler: async (response: { razorpay_order_id: string; razorpay_payment_id: string; razorpay_signature: string }) => {
          try {
            await api.post("/account/razorpay/verify", {
              plan_id: selectedPlan.id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_signature: response.razorpay_signature,
            });
            toast.success(`Payment successful — your plan is now ${selectedPlan.name}`);
            setSelectedPlan(null);
            await load();
          } catch (err) {
            toast.error(err instanceof Error ? err.message : "Payment verification failed");
          } finally {
            setChanging(false);
            setConfirming(false);
          }
        },
        modal: {
          ondismiss: () => {
            setChanging(false);
            setConfirming(false);
          },
        },
      };

      const rzp = new Razorpay(options);
      rzp.open();
      // changing stays true until handler or dismiss
      return;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to initiate payment");
      setChanging(false);
      setConfirming(false);
    }
  };

  const usageFor = (key: string): number => data?.usage?.[key] ?? 0;

  const renderLimitRow = (plan: Plan) => {
    const rows = LIMIT_LABELS.filter((l) => l.key in (plan.limits ?? {}));
    if (rows.length === 0) {
      return <p className="text-sm text-muted-foreground">{plan.description || plan.name}</p>;
    }
    return rows.map((row) => (
      <li key={row.key} className="flex items-center gap-2 text-sm">
        <Check className="size-4 shrink-0 text-primary" />
        <span className="text-muted-foreground">{row.label}</span>
        <span className="ml-auto font-medium text-foreground">
          {formatLimitValue(plan.limits[row.key])}
          {row.unit ? ` ${row.unit}` : ""}
        </span>
      </li>
    ));
  };

  const daysUntilExpiry = (() => {
    const end = data?.subscription?.current_period_end;
    if (!end) return null;
    const diff = new Date(end).getTime() - Date.now();
    const days = Math.ceil(diff / (1000 * 60 * 60 * 24));
    return days >= 0 && days <= 3 ? days : null;
  })();

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Billing &amp; Plans</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Manage your subscription and choose the plan that fits your business.
        </p>
      </div>

      {daysUntilExpiry !== null && data?.plan && data.plan.monthly_price_cents > 0 && (
        <div className="flex flex-col items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 sm:flex-row">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-amber-500/20 text-amber-600">
            <CreditCard className="size-4" />
          </div>
          <div className="flex-1">
            <p className="text-sm font-semibold text-amber-700 dark:text-amber-300">
              Your {data.plan.name} plan expires in {daysUntilExpiry} {daysUntilExpiry === 1 ? "day" : "days"} — {data.subscription?.current_period_end ? new Date(data.subscription.current_period_end).toLocaleDateString() : ""}
            </p>
            <p className="mt-1 text-xs text-amber-600/80 dark:text-amber-400/80">
              Renew now to avoid service interruption. You’ll also get an email reminder and a notification in your dashboard bell.
            </p>
          </div>
          <Button
            size="sm"
            className="w-full shrink-0 bg-amber-600 text-white hover:bg-amber-700 sm:w-auto"
            onClick={() => {
              const el = document.getElementById("plans-grid");
              el?.scrollIntoView({ behavior: "smooth" });
            }}
          >
            Renew
          </Button>
        </div>
      )}

      {loading ? (
        <div className="space-y-6">
          <Skeleton className="h-32 w-full" />
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <Skeleton className="h-72 w-full" />
            <Skeleton className="h-72 w-full" />
            <Skeleton className="h-72 w-full" />
          </div>
        </div>
      ) : !data ? (
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            Could not load billing information.{" "}
            <button onClick={load} className="text-primary hover:underline">
              Try again
            </button>
          </CardContent>
        </Card>
      ) : (
        <>
          {/* ── Current plan summary ── */}
          <Card>
            <CardHeader className="flex flex-col items-start justify-between gap-3 space-y-0 sm:flex-row sm:items-center">
              <div className="space-y-1">
                <CardTitle className="flex items-center gap-2">
                  <Sparkles className="size-5 shrink-0 text-primary" />
                  <span className="break-words">{data.plan ? data.plan.name : "No plan assigned"}</span>
                </CardTitle>
                <CardDescription>
                  {data.plan
                    ? `${formatPrice(data.plan.monthly_price_cents)}/month · ${data.plan.period}`
                    : "Contact support to get started."}
                </CardDescription>
              </div>
              {data.subscription && (
                <Badge variant={data.subscription.status === "active" ? "default" : "secondary"}>
                  {data.subscription.status}
                </Badge>
              )}
            </CardHeader>
            <CardContent className="grid gap-6 md:grid-cols-2">
              <div className="space-y-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  This month&apos;s usage
                </p>
                <div className="space-y-3">
                  {LIMIT_LABELS.map((row) => {
                    const limit = data.plan?.limits?.[row.key] ?? null;
                    const used = usageFor(row.key);
                    const pct = limit == null ? 0 : Math.min(100, Math.round((used / limit) * 100));
                    return (
                      <div key={row.key}>
                        <div className="flex items-center justify-between text-xs">
                          <span className="text-muted-foreground">{row.label}</span>
                          <span className="font-medium">
                            {used} {row.unit ?? ""}
                            {limit != null && (
                              <span className="text-muted-foreground">
                                {" "}
                                of {limit} {row.unit ?? ""}
                              </span>
                            )}
                          </span>
                        </div>
                        <div className="mt-1 h-1.5 rounded-full bg-muted">
                          {limit != null && (
                            <div
                              className={`h-1.5 rounded-full ${pct >= 90 ? "bg-destructive" : "bg-primary"}`}
                              style={{ width: `${Math.max(pct, 2)}%` }}
                            />
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="space-y-3 text-sm">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Subscription
                </p>
                {data.subscription ? (
                  <dl className="space-y-2">
                    <div className="flex justify-between gap-3">
                      <dt className="text-muted-foreground">Billing cycle</dt>
                      <dd className="break-words text-right font-medium capitalize">{data.subscription.status}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-muted-foreground">Renews</dt>
                      <dd className="text-right font-medium">
                        {data.subscription.current_period_end
                          ? new Date(data.subscription.current_period_end).toLocaleDateString()
                          : "—"}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-muted-foreground">Organization</dt>
                      <dd className="break-words text-right font-medium">{user?.org_name || "—"}</dd>
                    </div>
                  </dl>
                ) : (
                  <p className="text-muted-foreground">
                    No active subscription yet — pick a plan below to get started.
                  </p>
                )}
              </div>
            </CardContent>
          </Card>

          {/* ── Plan cards ── */}
          <div id="plans-grid">
            <h2 className="mb-1 text-lg font-semibold">Plans</h2>
            <p className="mb-4 text-sm text-muted-foreground">
              Upgrade or downgrade anytime. Changes apply immediately.
            </p>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {data.catalog.map((plan) => {
                const isCurrent = plan.id === currentPlanId;
                return (
                  <Card
                    key={plan.id}
                    className={`flex min-w-0 flex-col ${isCurrent ? "border-primary/50 ring-1 ring-primary/30" : ""}`}
                  >
                    <CardHeader>
                      <div className="flex items-start justify-between gap-2">
                        <CardTitle className="min-w-0 break-words text-lg">{plan.name}</CardTitle>
                        {isCurrent && (
                          <Badge className="shrink-0 bg-primary text-primary-foreground">Current</Badge>
                        )}
                      </div>
                      <div className="flex items-baseline gap-1">
                        <span className="text-3xl font-bold tracking-tight">
                          {formatPrice(plan.monthly_price_cents)}
                        </span>
                        <span className="text-sm text-muted-foreground">/month</span>
                      </div>
                      {plan.description && (
                        <CardDescription>{plan.description}</CardDescription>
                      )}
                    </CardHeader>
                    <CardContent className="flex-1">
                      <ul className="space-y-2">{renderLimitRow(plan)}</ul>
                    </CardContent>
                    <CardFooter>
                      <Button
                        variant={isCurrent ? "outline" : "default"}
                        disabled={isCurrent}
                        className="w-full"
                        onClick={() => {
                          setSelectedPlan(plan);
                          setConfirming(true);
                        }}
                      >
                        {isCurrent ? "Current plan" : plan.monthly_price_cents > (data.plan?.monthly_price_cents ?? 0) ? "Upgrade" : "Downgrade"}
                      </Button>
                    </CardFooter>
                  </Card>
                );
              })}
            </div>
          </div>
        </>
      )}

      {/* ── Confirmation dialog ── */}
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent className="w-[calc(100%-2rem)] max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle>
              Switch to the {selectedPlan?.name} plan?
            </AlertDialogTitle>
            <AlertDialogDescription>
              {selectedPlan?.monthly_price_cents ?? 0 > (data?.plan?.monthly_price_cents ?? 0)
                ? "Your new plan takes effect immediately."
                : "You'll be downgraded immediately. Limits from your new plan apply right away."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={changing}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={changing}
              onClick={handleChange}
              className="bg-primary text-primary-foreground hover:bg-primary/90"
            >
              {changing && <Loader2 className="size-4 animate-spin" />}
              {changing ? "Switching…" : "Switch plan"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}