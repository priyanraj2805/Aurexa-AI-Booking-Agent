"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { api } from "@/lib/api";
import { toast } from "sonner";
import { LoaderPulseSquare } from "@/components/ui/loaders";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  ChevronLeft,
  ChevronRight,
  CheckCircle2,
  Clock,
  Globe,
  Bot,
  Zap,
  Rocket,
} from "lucide-react";

const INDUSTRY_TYPES = [
  "Medical/Healthcare",
  "Beauty/Salon",
  "Consulting/Professional Services",
  "Fitness/Wellness",
  "Education/Tutoring",
  "Automotive",
  "Real Estate",
  "Legal/Financial",
  "Home Services",
  "Events/Entertainment",
  "Other",
];

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const STEPS = [
  { num: 1, label: "Business" },
  { num: 2, label: "Hours" },
  { num: 3, label: "Agent" },
  { num: 4, label: "Review" },
];

interface DayRow {
  active: boolean;
  open: string;
  close: string;
}

const defaultHours = (): DayRow[] =>
  DAY_NAMES.map((_, i) => ({
    active: i < 5,
    open: "09:00",
    close: "17:00",
  }));

export default function OnboardingPage() {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [checking, setChecking] = useState(true);

  const [industryType, setIndustryType] = useState("");
  const [timezone, setTimezone] = useState("UTC");
  const [slotDuration, setSlotDuration] = useState("30");
  const [bookingWindow, setBookingWindow] = useState("90");
  const [hours, setHours] = useState<DayRow[]>(defaultHours);
  const [agentName, setAgentName] = useState("Booking Assistant");
  const [greeting, setGreeting] = useState("Hello! How can I help you book an appointment today?");
  const [tone, setTone] = useState("friendly");
  const [requireOtp, setRequireOtp] = useState(true);

  // Auth guard + timezone detection + already-completed check
  useEffect(() => {
    if (isLoading) return;
    if (!user) {
      router.replace("/login");
      return;
    }
    setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
    api
      .get<{ onboarding_completed: boolean }>("/dashboard/onboarding/status")
.then((data) => {
          if (data.onboarding_completed) {
            router.replace("/dashboard");
            router.refresh();
          } else {
            setChecking(false);
          }
        })
      .catch(() => setChecking(false));
  }, [isLoading, user, router]);

  if (isLoading || checking || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-background">
        <LoaderPulseSquare className="size-16 text-primary" />
      </div>
    );
  }

  const canProceed = () => {
    if (step === 1) return industryType.trim().length > 0 && timezone.length > 0;
    if (step === 2) return hours.some((h) => h.active);
    return true;
  };

  const updateDay = (i: number, patch: Partial<DayRow>) => {
    setHours((prev) => prev.map((d, idx) => (idx === i ? { ...d, ...patch } : d)));
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    try {
      await api.post("/dashboard/onboarding", {
        business: { industry_type: industryType.trim() },
        hours: {
          timezone,
          slot_duration_minutes: Number(slotDuration),
          booking_window_days: Number(bookingWindow),
          weekly_schedule: hours.map((h, i) => ({
            day_of_week: i,
            is_active: h.active,
            open_time: h.active ? h.open : null,
            close_time: h.active ? h.close : null,
            break_start: null,
            break_end: null,
          })),
        },
        agent: {
          agent_name: agentName.trim() || "Booking Assistant",
          greeting_message: greeting.trim(),
          tone,
          require_otp: requireOtp,
        },
      });
      toast.success("Setup complete — welcome aboard!");
      router.replace("/dashboard");
      router.refresh();
      // Hard navigation fallback — router.replace can be swallowed by layout/middleware caching
      setTimeout(() => {
        if (typeof window !== "undefined") window.location.href = "/dashboard";
      }, 300);
    } catch (err) {
      console.error("Onboarding submit error:", err);
      toast.error(err instanceof Error ? err.message : "Failed to complete setup");
    } finally {
      setSubmitting(false);
    }
  };

  const activeDays = hours.filter((h) => h.active).length;

  const renderStep = () => {
    switch (step) {
      case 1:
        return (
          <div className="space-y-5">
            <div className="space-y-1.5">
              <Label htmlFor="industry">
                Industry type <span className="text-red-500">*</span>
              </Label>
              <Select value={industryType} onValueChange={(v) => setIndustryType(v ?? "")}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Select your industry" />
                </SelectTrigger>
                <SelectContent>
                  {INDUSTRY_TYPES.map((type) => (
                    <SelectItem key={type} value={type}>
                      {type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Helps us tailor your assistant&apos;s behavior and default fields.
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="timezone">
                Timezone <span className="text-red-500">*</span>
              </Label>
              <Select value={timezone} onValueChange={(v) => setTimezone(v ?? "UTC")}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Select timezone" />
                </SelectTrigger>
                <SelectContent className="max-h-64">
                  {(Intl.supportedValuesOf ? Intl.supportedValuesOf("timeZone") : ["UTC"]).map(
                    (tz: string) => (
                      <SelectItem key={tz} value={tz}>
                        {tz}
                      </SelectItem>
                    )
                  )}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Detected from your browser. All booking times use this zone.
              </p>
            </div>
          </div>
        );
      case 2:
        return (
          <div className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label>Slot duration</Label>
                <Select value={slotDuration} onValueChange={(v) => setSlotDuration(v ?? "30")}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="15">15 minutes</SelectItem>
                    <SelectItem value="30">30 minutes</SelectItem>
                    <SelectItem value="45">45 minutes</SelectItem>
                    <SelectItem value="60">60 minutes</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Booking window</Label>
                <Select value={bookingWindow} onValueChange={(v) => setBookingWindow(v ?? "90")}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="30">30 days ahead</SelectItem>
                    <SelectItem value="60">60 days ahead</SelectItem>
                    <SelectItem value="90">90 days ahead</SelectItem>
                    <SelectItem value="180">180 days ahead</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="rounded-xl border border-border overflow-hidden">
              <div className="flex items-center gap-2 border-b border-border bg-secondary/40 px-4 py-3">
                <Clock className="size-4 text-primary" />
                <span className="text-sm font-semibold">Weekly business hours</span>
                <span className="ml-auto text-[11px] text-muted-foreground">{timezone}</span>
              </div>
              <div className="divide-y divide-border">
                {hours.map((h, i) => (
                  <div
                    key={i}
                    className={`flex flex-wrap items-center gap-3 px-4 py-3 transition-colors ${
                      h.active ? "" : "opacity-50"
                    }`}
                  >
                    <label className="flex items-center gap-2.5 min-w-[130px] cursor-pointer">
                      <Switch
                        checked={h.active}
                        onCheckedChange={(v: boolean) => updateDay(i, { active: v })}
                        aria-label={`Toggle ${DAY_NAMES[i]}`}
                      />
                      <span className="text-sm font-medium">{DAY_NAMES[i]}</span>
                    </label>
                    {h.active ? (
                      <div className="flex items-center gap-2">
                        <Input
                          type="time"
                          value={h.open}
                          onChange={(e) => updateDay(i, { open: e.target.value })}
                          className="w-28"
                          aria-label={`${DAY_NAMES[i]} opening time`}
                        />
                        <span className="text-xs text-muted-foreground">to</span>
                        <Input
                          type="time"
                          value={h.close}
                          onChange={(e) => updateDay(i, { close: e.target.value })}
                          className="w-28"
                          aria-label={`${DAY_NAMES[i]} closing time`}
                        />
                      </div>
                    ) : (
                      <span className="text-xs text-muted-foreground">Closed all day</span>
                    )}
                  </div>
                ))}
              </div>
            </div>
            {activeDays === 0 && (
              <p className="text-sm text-red-500">
                At least one day must be open for bookings.
              </p>
            )}
          </div>
        );
      case 3:
        return (
          <div className="space-y-5">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="agentName">Agent name</Label>
                <Input
                  id="agentName"
                  value={agentName}
                  onChange={(e) => setAgentName(e.target.value)}
                  placeholder="Booking Assistant"
                />
              </div>
              <div className="space-y-1.5">
                <Label>Tone</Label>
                <Select value={tone} onValueChange={(v) => setTone(v ?? "friendly")}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="professional">Professional</SelectItem>
                    <SelectItem value="friendly">Friendly</SelectItem>
                    <SelectItem value="casual">Casual</SelectItem>
                    <SelectItem value="formal">Formal</SelectItem>
                    <SelectItem value="empathetic">Empathetic</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="greeting">Greeting message</Label>
              <Input
                id="greeting"
                value={greeting}
                onChange={(e) => setGreeting(e.target.value)}
                placeholder="Hello! How can I help you book an appointment today?"
              />
              <p className="text-xs text-muted-foreground">
                The first thing customers see when they open your widget.
              </p>
            </div>
            <div className="flex items-center gap-3 rounded-lg border border-border p-4">
              <Switch checked={requireOtp} onCheckedChange={setRequireOtp} id="requireOtp" />
              <div>
                <Label htmlFor="requireOtp" className="cursor-pointer">
                  Require OTP verification
                </Label>
                <p className="text-xs text-muted-foreground">
                  Customers confirm their email with a code before booking is confirmed.
                </p>
              </div>
            </div>
          </div>
        );
      case 4:
        return (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <Zap className="size-4 text-primary" /> Business
                </div>
                <p className="mt-2 text-sm text-muted-foreground">{industryType}</p>
                <p className="text-xs text-muted-foreground">Timezone: {timezone}</p>
              </div>
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <Clock className="size-4 text-primary" /> Hours
                </div>
                <p className="mt-2 text-sm text-muted-foreground">
                  {activeDays} active day{activeDays === 1 ? "" : "s"}
                </p>
                <p className="text-xs text-muted-foreground">
                  {slotDuration} min slots · {bookingWindow} days ahead
                </p>
              </div>
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <Bot className="size-4 text-primary" /> Agent
                </div>
                <p className="mt-2 text-sm text-muted-foreground">{agentName}</p>
                <p className="text-xs text-muted-foreground capitalize">{tone}</p>
              </div>
              <div className="rounded-xl border border-border p-4">
                <div className="flex items-center gap-2 text-sm font-semibold">
                  <CheckCircle2 className="size-4 text-primary" /> Security
                </div>
                <p className="mt-2 text-sm text-muted-foreground">
                  OTP {requireOtp ? "enabled" : "disabled"}
                </p>
              </div>
            </div>
            <p className="rounded-lg border border-amber-500/20 bg-amber-500/10 p-3 text-xs text-amber-600 dark:text-amber-400">
              You can change all of these later in Settings. Your booking assistant
              will use these details immediately after launch.
            </p>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-3xl px-4 py-10">
        {/* Header */}
        <div className="mb-6 text-center">
          <div className="mx-auto mb-3 flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Rocket className="size-6" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">Welcome to DotStark</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Complete these {STEPS.length} steps to unlock your dashboard.
          </p>
        </div>

        {/* Step indicator */}
        <div className="mb-6 flex items-center justify-center gap-2">
          {STEPS.map((s, i) => (
            <div key={s.num} className="flex items-center">
              <div
                className={`flex size-8 items-center justify-center rounded-full text-xs font-semibold transition-colors ${
                  step > s.num
                    ? "bg-primary text-primary-foreground"
                    : step === s.num
                      ? "border-2 border-primary text-primary"
                      : "border border-border text-muted-foreground"
                }`}
              >
                {step > s.num ? <CheckCircle2 className="size-4" /> : s.num}
              </div>
              <span
                className={`ml-1.5 hidden text-xs font-medium sm:inline ${
                  step >= s.num ? "text-foreground" : "text-muted-foreground"
                }`}
              >
                {s.label}
              </span>
              {i < STEPS.length - 1 && (
                <div
                  className={`mx-2 h-px w-8 sm:w-14 ${step > s.num ? "bg-primary" : "bg-border"}`}
                />
              )}
            </div>
          ))}
        </div>

        {/* Step content */}
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              Step {step} of {STEPS.length}: {STEPS[step - 1].label}
            </CardTitle>
            <CardDescription>
              {step === 1 && "Tell us about your business so we can tailor the assistant."}
              {step === 2 && "When can customers book appointments?"}
              {step === 3 && "Configure how your assistant sounds and behaves."}
              {step === 4 && "Double-check everything, then launch."}
            </CardDescription>
          </CardHeader>
          <CardContent>{renderStep()}</CardContent>
        </Card>

        {/* Navigation */}
        <div className="mt-6 flex items-center justify-between">
          <Button
            variant="outline"
            onClick={() => setStep((s) => Math.max(1, s - 1))}
            disabled={step === 1}
          >
            <ChevronLeft className="size-4" /> Back
          </Button>
          {step < 4 ? (
            <Button onClick={() => setStep((s) => Math.min(4, s + 1))} disabled={!canProceed()}>
              Continue <ChevronRight className="size-4" />
            </Button>
          ) : (
            <Button onClick={handleSubmit} disabled={submitting}>
              {submitting ? (
                <>
                  <LoaderPulseSquare className="size-4" /> Launching…
                </>
              ) : (
                <>
                  Complete setup <CheckCircle2 className="size-4" />
                </>
              )}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
