"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Globe,
  MessageCircle,
  CalendarDays,
  Copy,
  Check,
  Code2,
  ExternalLink,
  Eye,
  Rocket,
  Shield,
  Link2,
  Loader2,
  Save,
  Unlink,
  Calendar,
  Layers,
  Send,
  Hash,
  MessageSquare,
  Mail,
  AlertCircle,
  ChevronRight,
  Users,
  Workflow,
  Video,
  CreditCard,
  QrCode,
  RefreshCw,
} from "lucide-react";
import {
  useAgentConfig,
  useMeetingProvidersConfig,
  setActiveMeetingProvider,
  connectGoogleMeet,
  disconnectGoogleMeet,
  connectMicrosoftTeams,
  disconnectMicrosoftTeams,
  connectZoom,
  disconnectZoom,
  type AgentConfig,
  type MeetingProviderType,
} from "@/hooks/use-dashboard";
import { api } from "@/lib/api";
import { PaymentsConnectCard } from "@/components/payments-connect-card";
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
import { toast } from "sonner";

const API_BASE_FALLBACK = "/api/v1";
const PUBLIC_API_BASE_STORAGE_KEY = "dotstark_public_api_base";

function normalizeApiBase(raw: string | null | undefined): string {
  const value = (raw || "").trim();
  if (!value) return "";
  if (/^https?:\/\//i.test(value)) return value.replace(/\/+$/, "");
  if (value.startsWith("/")) return value.replace(/\/+$/, "");
  return `/${value}`.replace(/\/+$/, "");
}

function getRuntimeApiBase(): string {
  const envBase = normalizeApiBase(process.env.NEXT_PUBLIC_API_BASE || API_BASE_FALLBACK);
  if (!envBase) return API_BASE_FALLBACK;
  if (/^https?:\/\//i.test(envBase)) return envBase;
  if (typeof window === "undefined") return envBase;
  return `${window.location.origin}${envBase}`;
}

function getAbsoluteApiBase(): string {
  const base = getRuntimeApiBase();
  if (/^https?:\/\//i.test(base)) return base;
  if (typeof window !== "undefined") return `${window.location.origin}${base}`;
  return base;
}

function getApiRoot(apiBase: string): string {
  const normalized = normalizeApiBase(apiBase);
  return normalized.replace(/\/api\/v1\/?$/, "");
}

interface RuntimeUrls {
  frontend_url: string;
  widget_script_url: string;
  public_api_base: string;
  oauth_redirect_uris: Record<string, string>;
  warnings: string[];
}

interface GoogleCalendarConfig {
  google_calendar_enabled: boolean;
  google_calendar_connected: boolean;
}

interface CalendarConfig {
  google_calendar_connected: boolean;
  outlook_calendar_connected: boolean;
  active_calendar_provider: string | null;
}

interface GoogleCalendarState {
  connected: boolean;
  loading: boolean;
  connecting: boolean;
  disconnecting: boolean;
  error: string | null;
}

interface OutlookCalendarState {
  connected: boolean;
  loading: boolean;
  connecting: boolean;
  disconnecting: boolean;
  error: string | null;
}

interface WidgetTokenData {
  widget_token: string;
  masked_token: string;
}

interface MeetingProviderUiState {
  connecting: boolean;
  disconnecting: boolean;
}

export default function IntegrationsPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [activeTab, setActiveTab] = useState<"overview" | "widget" | "whatsapp" | "calendar" | "providers" | "payments">("overview");
  const [copied, setCopied] = useState(false);
  const [publicApiBaseInput, setPublicApiBaseInput] = useState("");
  const [runtimeUrls, setRuntimeUrls] = useState<RuntimeUrls | null>(null);
  const { data: config } = useAgentConfig();

  // Transactions table (Payments & Payouts tab), paginated.
  interface PayAttempt {
    id: string; provider: string; order_id: string | null; amount_paise: number;
    currency: string; status: string; created_at: string | null;
  }
  const PAY_PAGE_SIZE = 20;
  const [payAttempts, setPayAttempts] = useState<PayAttempt[]>([]);
  const [payAttemptsLoading, setPayAttemptsLoading] = useState(false);
  const [payPage, setPayPage] = useState(1);
  const [payTotal, setPayTotal] = useState(0);

  const loadPayAttempts = async (page: number) => {
    setPayAttemptsLoading(true);
    try {
      const offset = (page - 1) * PAY_PAGE_SIZE;
      const res = await api.get<{ attempts: PayAttempt[]; total: number }>(
        `/dashboard/payments/attempts?limit=${PAY_PAGE_SIZE}&offset=${offset}`
      );
      setPayAttempts(res.attempts ?? []);
      setPayTotal(Number(res.total ?? 0));
      setPayPage(page);
    } catch {
      /* table stays empty on error */
    } finally {
      setPayAttemptsLoading(false);
    }
  };
  useEffect(() => {
    if (activeTab !== "payments") return;
    setPayPage(1);
    loadPayAttempts(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);
  const payTotalPages = Math.max(1, Math.ceil(payTotal / PAY_PAGE_SIZE));
  const payFrom = payTotal === 0 ? 0 : (payPage - 1) * PAY_PAGE_SIZE + 1;
  const payTo = Math.min(payTotal, payPage * PAY_PAGE_SIZE);

  // WhatsApp QR pairing state is declared below (qrStatus, qrImg, ...).

  // WhatsApp QR pairing state (Baileys transport; replaces manual token setup)
  type QrConnection = {
    status: string; phone_number?: string; display_name?: string;
    connected_at?: string; disconnected_at?: string; last_seen_at?: string;
    booking_enabled?: boolean; booking_active?: boolean; booking_inactive_reason?: string | null;
  };
  const [qrStatus, setQrStatus] = useState<QrConnection | null>(null);
  const [qrLoading, setQrLoading] = useState(true);
  const [qrImg, setQrImg] = useState<{ dataUrl: string; expiresAt: number } | null>(null);
  const [qrBusy, setQrBusy] = useState(false);
  const [qrMsg, setQrMsg] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [qrPairing, setQrPairing] = useState(false);
  // True while the backend has no fresh QR to serve (socket rotating or
  // reconnecting). Shows an explicit waiting note instead of a blank box.
  const [qrWaiting, setQrWaiting] = useState(false);

  const loadQrStatus = async () => {
    try {
      const data = await api.get<QrConnection>("/dashboard/whatsapp-qr/status");
      setQrStatus(data);
      return data;
    } catch {
      setQrStatus(null);
      return null;
    } finally {
      setQrLoading(false);
    }
  };
  const loadQrImage = async () => {
    try {
      const data = await api.get<{ qr_data_url: string; issued_at: number; expires_at: number }>("/dashboard/whatsapp-qr/qr");
      // A new QR from the server always replaces the old frame — scanning a
      // stale code is the #1 "couldn't connect" cause on the phone.
      setQrImg({ dataUrl: data.qr_data_url, expiresAt: data.expires_at });
      setQrWaiting(false);
    } catch {
      // NO_FRESH_QR (socket between rotations) or not started. Clear any
      // expired frame so the user never scans a dead code, and show the
      // waiting note instead of a silent blank box.
      setQrImg((prev) => {
        if (prev && prev.expiresAt * 1000 < Date.now()) return null;
        return prev;
      });
      setQrWaiting(true);
    }
  };
  const startQrPairing = async () => {
    setQrBusy(true);
    setQrMsg(null);
    try {
      const data = await api.post<QrConnection>("/dashboard/whatsapp-qr/connect/start", {});
      setQrStatus(data);
      setQrPairing(true);
      await loadQrImage();
      toast.success("Scan the QR with your business WhatsApp number");
    } catch (err) {
      setQrMsg({ type: "error", text: err instanceof Error ? err.message : "Failed to start pairing" });
    } finally {
      setQrBusy(false);
    }
  };
  const disconnectQr = async () => {
    setQrBusy(true);
    try {
      const data = await api.post<QrConnection>("/dashboard/whatsapp-qr/disconnect", {});
      setQrStatus(data);
      setQrImg(null);
      setQrPairing(false);
      toast.success("WhatsApp disconnected");
    } catch (err) {
      setQrMsg({ type: "error", text: err instanceof Error ? err.message : "Failed to disconnect" });
    } finally {
      setQrBusy(false);
    }
  };
  const reconnectQr = async () => {
    setQrBusy(true);
    try {
      const data = await api.post<QrConnection>("/dashboard/whatsapp-qr/reconnect", {});
      setQrStatus(data);
      toast.success("Reconnect requested");
    } catch (err) {
      setQrMsg({ type: "error", text: err instanceof Error ? err.message : "Failed to reconnect" });
    } finally {
      setQrBusy(false);
    }
  };
  const toggleQrBooking = async (enabled: boolean) => {
    setQrBusy(true);
    try {
      const data = await api.post<QrConnection>("/dashboard/whatsapp-qr/booking-toggle", { enabled });
      setQrStatus(data);
      toast.success(enabled ? "WhatsApp booking enabled" : "WhatsApp booking disabled");
    } catch (err) {
      setQrMsg({ type: "error", text: err instanceof Error ? err.message : "Failed to update booking toggle" });
    } finally {
      setQrBusy(false);
    }
  };
  // Poll status + QR while the tab is open and pairing is active or connected.
  // NOTE: QR polling is gated on the *server* status (PENDING_QR /
  // DISCONNECTED), not only on the in-memory qrPairing flag — a page reload
  // resets qrPairing to false but the backend session is still waiting for a
  // scan, so the QR must keep refreshing or the user sees a dead screen.
  const qrPairingRef = useRef(false);
  qrPairingRef.current = qrPairing;
  useEffect(() => {
    if (activeTab !== "whatsapp") return;
    loadQrStatus();
    const t = setInterval(async () => {
      const s = await loadQrStatus();
      if (!s) return;
      if (s.status === "CONNECTED" || s.status === "LOGGED_OUT") {
        setQrPairing(false);
        setQrWaiting(false);
        if (s.status === "CONNECTED") setQrImg(null);
        return;
      }
      if (qrPairingRef.current || s.status === "PENDING_QR" || s.status === "DISCONNECTED") {
        await loadQrImage();
      }
    }, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  // Google Calendar state
  const [gcState, setGcState] = useState<GoogleCalendarState>({
    connected: false,
    loading: true,
    connecting: false,
    disconnecting: false,
    error: null,
  });

  // Outlook Calendar state
  const [ocState, setOcState] = useState<OutlookCalendarState>({
    connected: false,
    loading: true,
    connecting: false,
    disconnecting: false,
    error: null,
  });

  // Unified calendar config
  const [activeProvider, setActiveProvider] = useState<string | null>(null);
  const [settingActiveProvider, setSettingActiveProvider] = useState(false);
  const [migrationInfo, setMigrationInfo] = useState<string | null>(null);
  const [showMigrationDialog, setShowMigrationDialog] = useState(false);
  const [pendingProvider, setPendingProvider] = useState<string | null>(null);
  const [confirmAction, setConfirmAction] = useState<"regenerate_token" | "disconnect_google" | "disconnect_outlook" | null>(null);
  const [meetingMode, setMeetingMode] = useState<"OFFLINE_ONLY" | "ONLINE_ONLY" | "HYBRID">("OFFLINE_ONLY");
  const [allowHybridSelection, setAllowHybridSelection] = useState(true);
  const [meetingProviderDefault, setMeetingProviderDefault] = useState<"NONE" | "GOOGLE_MEET" | "MICROSOFT_TEAMS" | "ZOOM">("NONE");
  const [policySaving, setPolicySaving] = useState(false);
  const [pendingMeetingMode, setPendingMeetingMode] = useState<"OFFLINE_ONLY" | "ONLINE_ONLY" | null>(null);
  const [showMeetingPolicyWarning, setShowMeetingPolicyWarning] = useState(false);
  const {
    data: meetingProviders,
    loading: meetingProvidersLoading,
    refetch: refetchMeetingProviders,
  } = useMeetingProvidersConfig();
  const [meetingProviderSaving, setMeetingProviderSaving] = useState(false);
  const [meetingProviderUi, setMeetingProviderUi] = useState<Record<string, MeetingProviderUiState>>({
    GOOGLE_MEET: { connecting: false, disconnecting: false },
    MICROSOFT_TEAMS: { connecting: false, disconnecting: false },
    ZOOM: { connecting: false, disconnecting: false },
  });

  useEffect(() => {
    const policy = config?.meeting_policy;
    if (!policy) return;
    setMeetingMode(policy.meeting_mode);
    setAllowHybridSelection(policy.allow_hybrid_selection);
    setMeetingProviderDefault(policy.meeting_provider_default);
  }, [config]);

  // Widget Token state
  const [widgetToken, setWidgetToken] = useState<WidgetTokenData | null>(null);
  const [wtLoading, setWtLoading] = useState(true);
  const [wtRegenerating, setWtRegenerating] = useState(false);
  const [wtCopied, setWtCopied] = useState(false);
  const [wtRevealed, setWtRevealed] = useState(false);
  const [justRegenerated, setJustRegenerated] = useState(false);

  // Razorpay Route provider payouts
  type RouteSnapshot = {
    onboarding_status: string; payment_mode: string; razorpay_account_id?: string;
    razorpay_status?: string; route_product_id?: string; route_product_status?: string;
    stakeholder_id?: string; bank_verified: boolean; bank_last4?: string;
    beneficiary_name?: string; transfer_ready: boolean; transfer_ready_at?: string;
    failure_reason?: string; message: string;
    price_enabled: boolean; price_paise: number; currency: string;
  };
  const [routeStatus, setRouteStatus] = useState<RouteSnapshot | null>(null);
  const [routeAvailable, setRouteAvailable] = useState(false);
  const [routeLoading, setRouteLoading] = useState(true);
  const [routeBusy, setRouteBusy] = useState(false);
  const [routePhone, setRoutePhone] = useState("");
  const [routeLegalName, setRouteLegalName] = useState("");
  const [routeBizType, setRouteBizType] = useState("proprietorship");
  const [routeLegalPan, setRouteLegalPan] = useState("");
  const [routeLegalGst, setRouteLegalGst] = useState("");
  const [routeStreet1, setRouteStreet1] = useState("");
  const [routeStreet2, setRouteStreet2] = useState("");
  const [routeCity, setRouteCity] = useState("");
  const [routeState, setRouteState] = useState("");
  const [routePostal, setRoutePostal] = useState("");
  const [stakeName, setStakeName] = useState("");
  const [stakePan, setStakePan] = useState("");
  const [settleAcct, setSettleAcct] = useState("");
  const [settleIfsc, setSettleIfsc] = useState("");
  const [settleName, setSettleName] = useState("");
  const [routeTnc, setRouteTnc] = useState(false);
  const [routePriceEnabled, setRoutePriceEnabled] = useState(false);
  const [routePricePaise, setRoutePricePaise] = useState(0);
  const [routeCurrency, setRouteCurrency] = useState("INR");
  const [routeSavingPrice, setRouteSavingPrice] = useState(false);

  const loadRouteStatus = async () => {
    setRouteLoading(true);
    try {
      const data = await api.get<RouteSnapshot>("/dashboard/razorpay/route/status");
      setRouteStatus(data);
      setRoutePriceEnabled(data.price_enabled);
      setRoutePricePaise(data.price_paise || 0);
      setRouteCurrency(data.currency || "INR");
      setRouteAvailable(true);
    } catch (err) {
      // 404 = Route disabled on backend -> hide the card; other errors keep it hidden too
      setRouteAvailable(false);
    }
    setRouteLoading(false);
  };
  const routeCall = async (fn: () => Promise<RouteSnapshot>, okMsg: string) => {
    setRouteBusy(true);
    try {
      const data = await fn();
      setRouteStatus(data);
      setRouteAvailable(true);
      toast.success(okMsg);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Route request failed");
    } finally { setRouteBusy(false); }
  };
  // Docs KYC table: company-type entities must provide Business PAN (+ GST per
  // the KYC matrix); individual/not_yet_registered rely on stakeholder PAN.
  const ROUTE_COMPANY_TYPES = new Set([
    "llp", "partnership", "private_limited", "public_limited",
    "trust", "ngo", "society", "educational_institutes", "other",
  ]);
  const needsLegalPan = ROUTE_COMPANY_TYPES.has(routeBizType);
  const needsLegalGst = ROUTE_COMPANY_TYPES.has(routeBizType);
  const handleRouteStart = () => routeCall(
    () => api.post<RouteSnapshot>("/dashboard/razorpay/route/linked-account", {
      phone: routePhone,
      legal_business_name: routeLegalName || undefined,
      business_type: routeBizType,
      street1: routeStreet1,
      street2: routeStreet2 || undefined,
      city: routeCity,
      state: routeState,
      postal_code: routePostal,
      legal_pan: routeLegalPan || undefined,
      legal_gst: routeLegalGst || undefined,
    }),
    "Payout account created",
  );
  const handleRouteStakeholder = () => routeCall(
    () => api.post<RouteSnapshot>("/dashboard/razorpay/route/stakeholder", { name: stakeName || undefined, pan: stakePan || undefined }),
    "Business details saved",
  );
  const handleRouteProduct = () => routeCall(
    () => api.post<RouteSnapshot>("/dashboard/razorpay/route/product", { tnc_accepted: routeTnc }),
    "Route product requested",
  );
  const handleRouteSettlement = () => routeCall(
    () => api.patch<RouteSnapshot>("/dashboard/razorpay/route/product", { account_number: settleAcct, ifsc_code: settleIfsc, beneficiary_name: settleName, tnc_accepted: routeTnc }),
    "Bank details submitted",
  );
  const handleRouteMode = (mode: string) => routeCall(
    () => api.patch<RouteSnapshot>("/dashboard/razorpay/route/payment-mode", { mode }),
    `Payout mode set to ${mode}`,
  );
  const handleSaveRoutePrice = async () => {
    setRouteSavingPrice(true);
    try {
      await api.put("/dashboard/razorpay/price", { enabled: routePriceEnabled, amount_paise: routePriceEnabled ? routePricePaise : 0, currency: routeCurrency });
      toast.success("Appointment price saved");
      await loadRouteStatus();
    } catch (err) { toast.error(err instanceof Error ? err.message : "Failed to save price"); }
    setRouteSavingPrice(false);
  };

  useEffect(() => {
    loadCalendarConfig();
    loadWidgetToken();
    loadRouteStatus();
    // Overview cards (e.g. WhatsApp API badge) render from this state —
    // without an initial load they permanently show "Configure".
    loadQrStatus();

    const fromQuery = normalizeApiBase(searchParams.get("public_api_base"));
    const fromStorage =
      typeof window !== "undefined"
        ? normalizeApiBase(window.localStorage.getItem(PUBLIC_API_BASE_STORAGE_KEY))
        : "";
    const initial = fromQuery || fromStorage;
    if (initial) {
      setPublicApiBaseInput(initial);
      if (typeof window !== "undefined") {
        window.localStorage.setItem(PUBLIC_API_BASE_STORAGE_KEY, initial);
      }
    }

    api
      .get<RuntimeUrls>("/dashboard/runtime-urls")
      .then((data) => setRuntimeUrls(data))
      .catch(() => {
        // non-fatal: integration page can still function without diagnostics
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const googleConnected = searchParams.get("google_connected") === "true";
    const outlookConnected = searchParams.get("outlook_connected") === "true";
    const errorCode = searchParams.get("error");
    if (!googleConnected && !outlookConnected && !errorCode) return;

    if (googleConnected) {
      toast.success("Google Calendar connected successfully.");
    }
    if (outlookConnected) {
      toast.success("Outlook Calendar connected successfully.");
    }
    if (errorCode) {
      const errorMessageMap: Record<string, string> = {
        org_not_found: "We could not find your organization while completing calendar connection.",
        no_code: "Calendar authorization was cancelled or incomplete. Please try again.",
        token_exchange_failed: "Calendar connection failed during token exchange. Please retry.",
        save_failed: "Calendar connection was granted but saving failed. Please retry.",
        outlook_connection_failed: "Outlook connection failed. Please retry.",
        outlook_unauthorized_client: "Outlook app is not enabled for your account type. Update Azure app registration tenant/audience settings.",
        outlook_misconfigured: "Outlook integration is misconfigured. Check Microsoft client ID, secret, and redirect URI.",
        google_misconfigured: "Google integration is misconfigured. Check Google client ID, secret, and redirect URI.",
      };
      toast.error(errorMessageMap[errorCode] ?? "Calendar connection failed.");
    }

    loadCalendarConfig();
    router.replace("/dashboard/integrations");
  }, [searchParams, router]);

  const loadCalendarConfig = async () => {
    setGcState((prev) => ({ ...prev, loading: true, error: null }));
    setOcState((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const data = await api.get<CalendarConfig>("/dashboard/calendar/config");
      setActiveProvider(data.active_calendar_provider);
      setGcState((prev) => ({ ...prev, connected: data.google_calendar_connected, loading: false }));
      setOcState((prev) => ({ ...prev, connected: data.outlook_calendar_connected, loading: false }));
    } catch {
      setGcState((prev) => ({ ...prev, loading: false, error: "Failed to load config" }));
      setOcState((prev) => ({ ...prev, loading: false, error: "Failed to load config" }));
    }
  };

  const setActiveProviderHandler = async (provider: string) => {
    const isSwitching = activeProvider && activeProvider !== "NONE" && provider !== "NONE" && provider !== activeProvider;
    
    if (isSwitching) {
      setPendingProvider(provider);
      setShowMigrationDialog(true);
      return;
    }
    
    await applyProviderChange(provider);
  };

  const applyProviderChange = async (provider: string) => {
    setSettingActiveProvider(true);
    try {
      const data = await api.put<{ status: string; migration_triggered: boolean }>("/dashboard/calendar/active-provider", { provider });
      setActiveProvider(provider);
      toast.success(provider === "NONE" ? "Calendar sync disabled." : `${provider} set as active calendar provider.`);
      if (data.migration_triggered) {
        setMigrationInfo("Migration started in background. Check back shortly.");
        setTimeout(() => setMigrationInfo(null), 5000);
        toast.info("Migration started for future bookings.");
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to update active provider");
      await loadCalendarConfig();
    }
    setSettingActiveProvider(false);
  };

  const handleMigrationConfirm = () => {
    setShowMigrationDialog(false);
    if (pendingProvider) {
      applyProviderChange(pendingProvider);
      setPendingProvider(null);
    }
  };

  const handleMigrationCancel = () => {
    setShowMigrationDialog(false);
    setPendingProvider(null);
  };

  const executeConfirmedAction = async () => {
    const action = confirmAction;
    setConfirmAction(null);
    if (action === "regenerate_token") {
      await regenerateWidgetToken();
      return;
    }
    if (action === "disconnect_google") {
      await disconnectGoogleCalendar();
      return;
    }
    if (action === "disconnect_outlook") {
      await disconnectOutlookCalendar();
    }
  };

  const loadWidgetToken = async () => {
    setWtLoading(true);
    try {
      const data = await api.get<WidgetTokenData>(`/dashboard/widget-token?t=${Date.now()}`);
      console.log("[WidgetToken] Loaded", data.masked_token);
      setWidgetToken(data);
    } catch (e) {
      console.log("[WidgetToken] Load failed", e);
      // Token doesn't exist yet — backend will generate on first GET
    }
    setWtLoading(false);
  };

  const regenerateWidgetToken = async () => {
    setWtRegenerating(true);
    const oldMasked = widgetToken?.masked_token || "";
    console.log("[WidgetToken] Regenerate clicked, old", oldMasked);
    try {
      const data = await api.post<WidgetTokenData>("/dashboard/widget-token/regenerate");
      console.log("[WidgetToken] Regenerated", data.widget_token, data.masked_token, "old", oldMasked, "changed", oldMasked !== data.masked_token);
      setWidgetToken(data);
      setWtRevealed(true);
      setJustRegenerated(true);
      setTimeout(() => setJustRegenerated(false), 6000);
      try {
        await navigator.clipboard.writeText(data.widget_token);
        toast.success(`Widget token regenerated! Old ${oldMasked} -> New ${data.masked_token} (copied)`);
      } catch {
        toast.success(`Widget token regenerated! ${oldMasked} -> ${data.masked_token} (now revealed)`);
      }
      setTimeout(() => setWtRevealed(false), 15000);
    } catch (err: unknown) {
      console.error("[WidgetToken] Regenerate failed", err);
      toast.error(err instanceof Error ? err.message : "Failed to regenerate widget token");
    }
    setWtRegenerating(false);
  };

  const connectGoogleCalendar = async () => {
    setGcState((prev) => ({ ...prev, connecting: true, error: null }));
    try {
      const data = await api.post<{ auth_url: string }>("/dashboard/google-calendar/connect");
      window.location.href = data.auth_url;
    } catch (err: unknown) {
      setGcState((prev) => ({ 
        ...prev, 
        connecting: false, 
        error: err instanceof Error ? err.message : "Failed to connect" 
      }));
    }
  };

  const disconnectGoogleCalendar = async () => {
    setGcState((prev) => ({ ...prev, disconnecting: true, error: null }));
    try {
      await api.post("/dashboard/google-calendar/disconnect");
      setGcState((prev) => ({ ...prev, connected: false, disconnecting: false }));
      if (activeProvider === "GOOGLE") {
        setActiveProvider("NONE");
      }
      await loadCalendarConfig();
      toast.success("Google Calendar disconnected.");
    } catch (err: unknown) {
      setGcState((prev) => ({ 
        ...prev, 
        disconnecting: false, 
        error: err instanceof Error ? err.message : "Failed to disconnect" 
      }));
      toast.error(err instanceof Error ? err.message : "Failed to disconnect Google Calendar");
    }
  };

  const connectOutlookCalendar = async () => {
    setOcState((prev) => ({ ...prev, connecting: true, error: null }));
    try {
      const data = await api.post<{ auth_url: string }>("/dashboard/outlook-calendar/connect");
      window.location.href = data.auth_url;
    } catch (err: unknown) {
      setOcState((prev) => ({ 
        ...prev, 
        connecting: false, 
        error: err instanceof Error ? err.message : "Failed to connect" 
      }));
    }
  };

  const disconnectOutlookCalendar = async () => {
    setOcState((prev) => ({ ...prev, disconnecting: true, error: null }));
    try {
      await api.post("/dashboard/outlook-calendar/disconnect");
      setOcState((prev) => ({ ...prev, connected: false, disconnecting: false }));
      if (activeProvider === "OUTLOOK") {
        setActiveProvider("NONE");
      }
      await loadCalendarConfig();
      toast.success("Outlook Calendar disconnected.");
    } catch (err: unknown) {
      setOcState((prev) => ({ 
        ...prev, 
        disconnecting: false, 
        error: err instanceof Error ? err.message : "Failed to disconnect" 
      }));
      toast.error(err instanceof Error ? err.message : "Failed to disconnect Outlook Calendar");
    }
  };

  const applyMeetingMode = (mode: "OFFLINE_ONLY" | "ONLINE_ONLY" | "HYBRID") => {
    if (meetingMode === "HYBRID" && mode !== "HYBRID") {
      setPendingMeetingMode(mode);
      setShowMeetingPolicyWarning(true);
      return;
    }
    setMeetingMode(mode);
  };

  const saveMeetingPolicy = async () => {
    setPolicySaving(true);
    try {
      const updated = await api.put<AgentConfig>("/dashboard/agent-config", {
        meeting_mode: meetingMode,
        allow_hybrid_selection: allowHybridSelection,
        meeting_provider_default: meetingProviderDefault,
      });
      const policy = updated.meeting_policy;
      setMeetingMode(policy.meeting_mode);
      setAllowHybridSelection(policy.allow_hybrid_selection);
      setMeetingProviderDefault(policy.meeting_provider_default);
      toast.success("Meeting policy updated.");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to save meeting policy");
    } finally {
      setPolicySaving(false);
    }
  };

  const handleActivateMeetingProvider = async (provider: MeetingProviderType) => {
    setMeetingProviderSaving(true);
    try {
      await setActiveMeetingProvider({ provider });
      await refetchMeetingProviders();
      toast.success(provider === "NONE" ? "Meeting provider deactivated." : `${provider} set as active meeting provider.`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to update active meeting provider");
    } finally {
      setMeetingProviderSaving(false);
    }
  };

  const handleMeetingProviderConnect = async (
    provider: "GOOGLE_MEET" | "MICROSOFT_TEAMS" | "ZOOM",
  ) => {
    setMeetingProviderUi((prev) => ({
      ...prev,
      [provider]: { ...prev[provider], connecting: true },
    }));
    try {
      const response =
        provider === "GOOGLE_MEET"
          ? await connectGoogleMeet()
          : provider === "MICROSOFT_TEAMS"
            ? await connectMicrosoftTeams()
            : await connectZoom();
      if (response.auth_url) {
        window.location.href = response.auth_url;
        return;
      }
      await refetchMeetingProviders();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : `Failed to connect ${provider}`);
    } finally {
      setMeetingProviderUi((prev) => ({
        ...prev,
        [provider]: { ...prev[provider], connecting: false },
      }));
    }
  };

  const handleMeetingProviderDisconnect = async (
    provider: "GOOGLE_MEET" | "MICROSOFT_TEAMS" | "ZOOM",
  ) => {
    setMeetingProviderUi((prev) => ({
      ...prev,
      [provider]: { ...prev[provider], disconnecting: true },
    }));
    try {
      if (provider === "GOOGLE_MEET") {
        await disconnectGoogleMeet();
      } else if (provider === "MICROSOFT_TEAMS") {
        await disconnectMicrosoftTeams();
      } else {
        await disconnectZoom();
      }
      await refetchMeetingProviders();
      toast.success(`${provider} disconnected.`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : `Failed to disconnect ${provider}`);
    } finally {
      setMeetingProviderUi((prev) => ({
        ...prev,
        [provider]: { ...prev[provider], disconnecting: false },
      }));
    }
  };

  const brandColor = config?.ui_brand_color || "#FF6B00";
  const rawToken = widgetToken?.widget_token || "ds_wid_your_token_here";
  const runtimeApiBase = getRuntimeApiBase();
  const selectedPublicApiBase = normalizeApiBase(publicApiBaseInput) || runtimeApiBase;
  const selectedPublicApiRoot = getApiRoot(selectedPublicApiBase);
  const oauthWarnings = runtimeUrls?.warnings ?? [];
  const oauthRedirectUris = runtimeUrls?.oauth_redirect_uris ?? {};

  const persistPublicApiBase = (value: string) => {
    const trimmed = (value || "").trim();
    if (!trimmed) {
      toast.error("Enter a public API base URL first (e.g. https://your-domain/api/v1).");
      return;
    }
    const normalized = normalizeApiBase(trimmed);
    setPublicApiBaseInput(normalized);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(PUBLIC_API_BASE_STORAGE_KEY, normalized);
    }
    toast.success("Public API base saved.");
  };

  const prodSnippet = `<!-- DotStark Booking Widget -->
<script
  src="${selectedPublicApiRoot}/static/widget/widget.js"
  data-widget-token="${rawToken}"
  data-api-base="${selectedPublicApiBase}"
  data-color="${brandColor}"
  defer>
</script>`;

  const handlePreview = () => {
    const absoluteApiBase = getAbsoluteApiBase();
    const absoluteApiRoot = getApiRoot(absoluteApiBase);
    const previewHtml = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Widget Preview</title>
  <style>
    body { 
      margin: 0; min-height: 100vh; background-color: #fafafa;
      background-image: linear-gradient(to right, rgba(0,0,0,0.05) 1px, transparent 1px), linear-gradient(to bottom, rgba(0,0,0,0.05) 1px, transparent 1px);
      background-size: 24px 24px; font-family: system-ui, -apple-system, sans-serif;
      display: flex; align-items: center; justify-content: center;
    }
    .preview-container {
      background: white; padding: 30px; border-radius: 12px;
      box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); text-align: center; max-width: 400px;
    }
  </style>
</head>
<body>
  <div class="preview-container">
    <h2>Website Simulation</h2>
    <p>Your DotStark AI booking widget will appear in the bottom-right corner.</p>
  </div>
  <script>
    window.DotStarkBookingConfig = {
      widgetToken: "${rawToken}",
      apiBase: "${absoluteApiBase}",
      primaryColor: "${brandColor}",
      forceNewSession: true,
      autoOpen: true
    };
    setTimeout(() => {
      const widget = document.querySelector('dotstark-booking-widget');
      if (widget && !widget.isOpen && widget._chatBox && typeof widget._toggle === 'function') {
        widget._toggle();
      }
    }, 2000);
  </script>
  <script src="${absoluteApiRoot}/static/widget/widget.js" defer></script>
</body>
</html>`;

    const blob = new Blob([previewHtml], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "width=800,height=800");
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(prodSnippet);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const activeIntegrations = [
    {
      id: "payments" as const,
      title: "Provider Payouts",
      icon: CreditCard,
      iconBg: "bg-emerald-500",
      status: (()=>{
        if(routeStatus?.transfer_ready) return "Ready";
        const s=(routeStatus?.onboarding_status||"NOT_STARTED");
        if(s==="UNDER_REVIEW") return "Under Review";
        if(s==="SUSPENDED"||s==="FAILED") return "Action Required";
        if(s!=="NOT_STARTED") return "Onboarding";
        return "Not Connected";
      })(),
      statusClass: (()=>{
        if(routeStatus?.transfer_ready) return "bg-green-500/10 text-green-500";
        const s=(routeStatus?.onboarding_status||"NOT_STARTED");
        if(s==="UNDER_REVIEW") return "bg-amber-500/10 text-amber-600";
        if(s==="SUSPENDED"||s==="FAILED") return "bg-red-500/10 text-red-600";
        if(s!=="NOT_STARTED") return "bg-sky-500/10 text-sky-600";
        return "bg-muted text-muted-foreground";
      })(),
      description: "Receive automatic payouts to your bank account.",
    },
    {
      id: "widget",
      title: "Universal Web Widget",
      icon: Globe,
      iconBg: "bg-blue-500",
      status: "Active",
      statusClass: "bg-green-500/10 text-green-500",
      description: "Embed the conversational AI as a floating widget on your website.",
    },
    {
      id: "whatsapp",
      title: "WhatsApp API",
      icon: MessageCircle,
      iconBg: "bg-green-500",
      status: qrStatus?.status === "CONNECTED" ? "Active" : "Configure",
      statusClass: qrStatus?.status === "CONNECTED" ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500",
      description: "Allow the AI to converse and take bookings directly via WhatsApp.",
    },
    {
      id: "calendar",
      title: "Calendar Sync",
      icon: CalendarDays,
      iconBg: "bg-indigo-500",
      status: activeProvider && activeProvider !== "NONE" ? `${activeProvider} Active` : (gcState.connected || ocState.connected ? "Connected" : "Not Connected"),
      statusClass: (gcState.loading || ocState.loading) ? "bg-yellow-500/10 text-yellow-500" : activeProvider && activeProvider !== "NONE" ? "bg-green-500/10 text-green-500" : (gcState.connected || ocState.connected) ? "bg-green-500/10 text-green-500" : "bg-muted/10 text-muted-foreground",
      description: "Sync confirmed appointments dynamically in real-time.",
    },
    {
      id: "providers",
      title: "Meeting Providers",
      icon: Video,
      iconBg: "bg-violet-500",
      status:
        meetingProviders?.active_meeting_provider && meetingProviders.active_meeting_provider !== "NONE"
          ? `${meetingProviders.active_meeting_provider.replace("_", " ")} Active`
          : meetingProviders?.google_meet_connected
            ? "Inactive"
            : "Not Connected",
      statusClass:
        meetingProviders?.active_meeting_provider && meetingProviders.active_meeting_provider !== "NONE"
          ? "bg-green-500/10 text-green-500"
          : meetingProviders?.google_meet_connected
            ? "bg-amber-500/10 text-amber-600"
            : "bg-muted/10 text-muted-foreground",
      description: "Connect Google Meet for online bookings.",
    },
  ];

  const comingSoonIntegrations = [
    { title: "Slack", icon: Hash, iconBg: "bg-purple-500" },
    { title: "HubSpot", icon: Link2, iconBg: "bg-orange-500" },
    { title: "Microsoft Teams", icon: MessageCircle, iconBg: "bg-[#6264A7]" },
    { title: "Zoom", icon: Video, iconBg: "bg-[#2D8CFF]" },
  ];

  return (
    <div className="flex flex-col h-full space-y-6">
      <div>
        <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">Integrations</h1>
        <p className="text-muted-foreground text-xs sm:text-sm mt-1">
          Connect your booking agent across multiple channels and synchronize availability.
        </p>
      </div>

      <div className="flex flex-col md:flex-row gap-8">
        <div className="w-full md:w-64 shrink-0 space-y-1">
          <button
            onClick={() => setActiveTab("overview")}
            className={`w-full flex items-center justify-between px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === "overview" 
              ? "bg-primary text-primary-foreground shadow-md" 
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            }`}
          >
            <div className="flex items-center gap-3">
              <Layers className="size-4" />
              Overview
            </div>
          </button>
          
          <div className="pt-4 pb-2 px-4 text-xs font-semibold text-muted-foreground uppercase tracking-wider">
            Active Integrations
          </div>
          
          <button
            onClick={() => setActiveTab("widget")}
            className={`w-full flex items-center justify-between px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === "widget" 
              ? "bg-primary text-primary-foreground shadow-md" 
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            }`}
          >
            <div className="flex items-center gap-3">
              <Globe className="size-4" />
              Web Widget
            </div>
            {widgetToken && <ChevronRight className="size-4 opacity-50" />}
          </button>

          <button
            onClick={() => setActiveTab("payments")}
            className={`w-full flex items-center justify-between px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === "payments" 
              ? "bg-primary text-primary-foreground shadow-md" 
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            }`}
          >
            <div className="flex items-center gap-3">
              <CreditCard className="size-4" />
              Payments & Payouts
            </div>
            {routeStatus?.transfer_ready && <ChevronRight className="size-4 opacity-50 text-green-500" />}
          </button>

          <button
            onClick={() => setActiveTab("whatsapp")}
            className={`w-full flex items-center justify-between px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === "whatsapp" 
              ? "bg-primary text-primary-foreground shadow-md" 
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            }`}
          >
            <div className="flex items-center gap-3">
              <MessageCircle className="size-4" />
              WhatsApp
            </div>
            {qrStatus?.status === "CONNECTED" && <ChevronRight className="size-4 opacity-50" />}
          </button>

          <button
            onClick={() => setActiveTab("calendar")}
            className={`w-full flex items-center justify-between px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === "calendar" 
              ? "bg-primary text-primary-foreground shadow-md" 
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            }`}
          >
            <div className="flex items-center gap-3">
              <CalendarDays className="size-4" />
              Calendar Sync
            </div>
            {(gcState.connected || ocState.connected) && <ChevronRight className="size-4 opacity-50" />}
          </button>

          <button
            onClick={() => setActiveTab("providers")}
            className={`w-full flex items-center justify-between px-4 py-3 rounded-lg text-sm font-medium transition-all duration-200 ${
              activeTab === "providers" 
              ? "bg-primary text-primary-foreground shadow-md" 
              : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            }`}
          >
            <div className="flex items-center gap-3">
              <Video className="size-4" />
              Meeting Providers
            </div>
            {meetingProviders?.active_meeting_provider && meetingProviders.active_meeting_provider !== "NONE" && <ChevronRight className="size-4 opacity-50" />}
          </button>
        </div>

        <div className="flex-1 max-w-4xl min-h-[500px]">
          {activeTab === "overview" && (
            <div className="space-y-8 animate-in fade-in duration-500 slide-in-from-bottom-2">
              <div className="space-y-4">
                <h3 className="text-lg font-semibold tracking-tight">Active Channels & Sync</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
                  {activeIntegrations.map((item) => (
                    <div 
                      key={item.title} 
                      onClick={() => setActiveTab(item.id as any)}
                      className="group cursor-pointer bg-card border border-border/60 hover:border-border rounded-xl p-5 flex flex-col transition-all duration-300 hover:shadow-lg hover:-translate-y-1 relative overflow-hidden"
                    >
                      <div className="absolute inset-0 bg-gradient-to-br from-primary/5 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none" />
                      <div className="flex items-center justify-between mb-4 relative z-10">
                        <div className={`flex items-center justify-center size-10 rounded-xl shadow-sm ${item.iconBg}`}>
                          <item.icon className="size-5 text-white" />
                        </div>
                        <span className={`text-[10px] font-bold uppercase px-2 py-1 rounded-full ${item.statusClass}`}>
                          {item.status}
                        </span>
                      </div>
                      <span className="font-semibold text-base mb-1.5 relative z-10">{item.title}</span>
                      <p className="text-[13px] text-muted-foreground leading-relaxed flex-1 relative z-10">
                        {item.description}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              <div className="space-y-4 pt-4 border-t border-border/50">
                <h3 className="text-lg font-semibold tracking-tight flex items-center gap-2">
                  <Rocket className="size-4 text-primary" />
                  Coming Soon
                </h3>
                <div className="flex flex-wrap gap-3">
                  {comingSoonIntegrations.map((item) => (
                    <div 
                      key={item.title} 
                      className="flex items-center gap-3 bg-secondary/30 border border-border/40 rounded-lg px-4 py-3"
                    >
                      <div className={`flex items-center justify-center size-8 rounded-lg opacity-80 ${item.iconBg}`}>
                        <item.icon className="size-4 text-white" />
                      </div>
                      <div>
                        <span className="font-medium text-sm text-foreground/80">{item.title}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {activeTab === "widget" && (
            <div className="space-y-6 animate-in fade-in duration-500 slide-in-from-bottom-2">
              <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5 bg-secondary/40 border-b border-border/50">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-blue-500/10 rounded-lg">
                      <Shield className="size-5 text-blue-500" />
                    </div>
                    <div>
                      <h3 className="font-semibold text-base leading-none">Widget Token</h3>
                      <p className="text-[13px] text-muted-foreground mt-1">Required to authenticate website widget requests</p>
                    </div>
                  </div>
                  {widgetToken && (
                    <button
                      onClick={() => { console.log("[WidgetToken] Regenerate button clicked"); setConfirmAction("regenerate_token"); }}
                      disabled={wtRegenerating}
                      className="flex items-center gap-2 text-[12px] font-medium px-3 py-1.5 rounded-md border border-amber-200 dark:border-amber-800 text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950 transition-colors disabled:opacity-50"
                    >
                      <Loader2 className={`size-3.5 ${wtRegenerating ? "animate-spin" : ""}`} />
                      {wtRegenerating ? "Generating..." : "Regenerate"}
                    </button>
                  )}
                </div>
                <div className="p-6 md:p-8">
                  {wtLoading ? (
                    <div className="flex items-center gap-3">
                      <Loader2 className="size-4 animate-spin text-muted-foreground" />
                      <span className="text-sm text-muted-foreground">Loading token...</span>
                    </div>
                  ) : widgetToken ? (
                    <div className="space-y-5">
                      <div>
                        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-2 block">
                          Your Active Token
                        </label>
                      <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                          <div className={`flex-1 flex items-center gap-3 border rounded-xl px-4 py-3 transition-all ${justRegenerated ? "bg-green-500/10 border-green-500/30 ring-2 ring-green-500/20" : "bg-secondary/30 border-border"}`}>
                            <Code2 className={`size-4 shrink-0 ${justRegenerated ? "text-green-600" : "text-muted-foreground"}`} />
                            <code className={`text-sm font-mono break-all tracking-tight ${justRegenerated ? "text-green-700 dark:text-green-300" : "text-foreground"}`}>
                              {wtRevealed ? widgetToken.widget_token : widgetToken.masked_token}
                            </code>
                          </div>
                          <button
                            onClick={() => { setWtRevealed((v) => !v); }}
                            className="flex items-center gap-2 text-sm font-medium px-4 py-3 rounded-xl border border-border hover:bg-accent transition-colors"
                          >
                            <Eye className="size-4" />
                            {wtRevealed ? "Hide" : "Reveal"}
                          </button>
                          <button
                            onClick={() => {
                              navigator.clipboard.writeText(widgetToken.widget_token);
                              setWtCopied(true);
                              setTimeout(() => setWtCopied(false), 2000);
                            }}
                            className="flex items-center gap-2 text-sm font-medium px-5 py-3 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shadow-sm"
                          >
                            {wtCopied ? <Check className="size-4" /> : <Copy className="size-4" />}
                            {wtCopied ? "Copied" : "Copy"}
                          </button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <div className="text-center py-8 bg-secondary/20 rounded-xl border border-dashed border-border">
                      <p className="text-sm text-muted-foreground mb-4">No widget token has been generated yet.</p>
                      <button
                        onClick={loadWidgetToken}
                        className="text-sm font-medium px-6 py-2.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 transition-colors shadow-sm"
                      >
                        Generate First Token
                      </button>
                    </div>
                  )}
                </div>
              </div>

              <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5 bg-secondary/40 border-b border-border/50">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-primary/10 rounded-lg">
                      <Code2 className="size-5 text-primary" />
                    </div>
                    <div>
                      <h3 className="font-semibold text-base leading-none">Embed Code</h3>
                    </div>
                  </div>
                </div>
                <div className="p-1">
                  <div className="bg-zinc-950 rounded-xl p-5 overflow-x-auto mx-4 my-3 ring-1 ring-white/10">
                    {wtLoading ? (
                      <div className="flex items-center gap-2 text-sm text-zinc-400 py-6 justify-center">
                        <Loader2 className="size-4 animate-spin" />
                      </div>
                    ) : (
                      <pre className="text-[13px] font-mono leading-relaxed text-zinc-300">
                        {prodSnippet}
                      </pre>
                    )}
                  </div>
                </div>
                <div className="px-6 py-4 bg-secondary/20 border-t border-border/50 flex flex-col sm:flex-row items-center justify-between gap-4">
                  <div className="flex items-center gap-2 text-xs text-muted-foreground w-full sm:w-auto">
                    <ExternalLink className="size-3.5" />
                    Lightweight (~8 KB gzip)
                  </div>
                  <div className="flex items-center gap-2 w-full sm:w-auto">
                    <button
                      onClick={handlePreview}
                      className="flex-1 sm:flex-none flex items-center justify-center gap-2 text-sm font-medium px-4 py-2 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm"
                    >
                      <Eye className="size-4" />
                      Preview
                    </button>
                    <button
                      onClick={handleCopy}
                      className="flex-1 sm:flex-none flex items-center justify-center gap-2 text-sm font-medium px-4 py-2 rounded-xl bg-secondary border border-border text-foreground hover:bg-accent transition-all"
                    >
                      {copied ? <Check className="size-4 text-green-500" /> : <Copy className="size-4" />}
                      {copied ? "Copied" : "Copy"}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {activeTab === "payments" && routeAvailable && (
            <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-sm animate-in fade-in duration-500 slide-in-from-bottom-2">
              <div className="flex flex-wrap items-center justify-between gap-2 sm:gap-3 p-4 sm:p-5 bg-secondary/40 border-b border-border/50">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-sky-500/10 rounded-lg"><CreditCard className="size-5 text-sky-600" /></div>
                  <div><h3 className="font-semibold text-base leading-none">Provider Payouts</h3><p className="text-xs text-muted-foreground mt-1">Razorpay Route • platform payments → automatic provider transfers</p></div>
                </div>
                {(()=>{
                  const s=(routeStatus?.onboarding_status||"NOT_STARTED");
                  if(routeStatus?.transfer_ready) return <span className="text-xs font-bold uppercase tracking-wider bg-green-500/10 text-green-600 px-3 py-1 rounded-full">Ready to receive payments</span>;
                  if(s==="UNDER_REVIEW") return <span className="text-xs font-bold uppercase tracking-wider bg-amber-500/10 text-amber-600 px-3 py-1 rounded-full">Verification in progress</span>;
                  if(s==="SUSPENDED"||s==="FAILED") return <span className="text-xs font-bold uppercase tracking-wider bg-red-500/10 text-red-600 px-3 py-1 rounded-full">Action required</span>;
                  if(s!=="NOT_STARTED") return <span className="text-xs font-bold uppercase tracking-wider bg-sky-500/10 text-sky-600 px-3 py-1 rounded-full">Onboarding</span>;
                  return <span className="text-xs font-bold uppercase tracking-wider bg-muted px-3 py-1 rounded-full text-muted-foreground">Not connected</span>;
                })()}
              </div>
              <div className="p-6 md:p-8 space-y-6">
                {routeLoading ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin"/> Loading payout status…</div> : (
                  <>
                    <p className="text-sm text-muted-foreground">{routeStatus?.message || "Connect a bank account to receive automatic payouts."}</p>
                    {(routeStatus?.onboarding_status||"NOT_STARTED")==="NOT_STARTED" && (
                      <div className="rounded-xl border border-dashed p-6 space-y-3 bg-secondary/20">
                        <div className="grid sm:grid-cols-2 gap-3">
                          <select value={routeBizType} onChange={e=>setRouteBizType(e.target.value)} className="bg-background border rounded-lg px-3 py-2 text-sm" aria-label="Business type">
                            <option value="individual">Individual</option>
                            <option value="proprietorship">Proprietorship</option>
                            <option value="partnership">Partnership</option>
                            <option value="private_limited">Private Limited</option>
                            <option value="public_limited">Public Limited</option>
                            <option value="llp">LLP</option>
                            <option value="trust">Trust</option>
                            <option value="ngo">NGO</option>
                            <option value="society">Society</option>
                            <option value="not_yet_registered">Not yet registered</option>
                            <option value="educational_institutes">Educational institute</option>
                            <option value="other">Other</option>
                          </select>
                          <input value={routePhone} onChange={e=>setRoutePhone(e.target.value)} placeholder="Business phone (8–15 digits)" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                        </div>
                        <input value={routeLegalName} onChange={e=>setRouteLegalName(e.target.value)} placeholder="Legal business name (optional)" className="bg-background border rounded-lg px-3 py-2 text-sm w-full" />
                        {routeBizType !== "individual" && routeBizType !== "not_yet_registered" && (
                          <div className="grid sm:grid-cols-2 gap-3">
                            <input value={routeLegalPan} onChange={e=>setRouteLegalPan(e.target.value.toUpperCase())} placeholder={needsLegalPan ? "Business PAN (required)" : "Business PAN (optional)"} maxLength={10} className="bg-background border rounded-lg px-3 py-2 text-sm" />
                            <input value={routeLegalGst} onChange={e=>setRouteLegalGst(e.target.value.toUpperCase())} placeholder={needsLegalGst ? "GSTIN (required)" : "GSTIN (if registered)"} maxLength={15} className="bg-background border rounded-lg px-3 py-2 text-sm" />
                          </div>
                        )}
                        <div>
                          <div className="text-xs text-muted-foreground mb-1.5">Registered business address (required for payout verification)</div>
                          <div className="grid sm:grid-cols-2 gap-3">
                            <input value={routeStreet1} onChange={e=>setRouteStreet1(e.target.value)} placeholder="Street / address line 1" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                            <input value={routeStreet2} onChange={e=>setRouteStreet2(e.target.value)} placeholder="Street line 2 (optional)" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                            <input value={routeCity} onChange={e=>setRouteCity(e.target.value)} placeholder="City" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                            <input value={routeState} onChange={e=>setRouteState(e.target.value)} placeholder="State" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                            <input value={routePostal} onChange={e=>setRoutePostal(e.target.value.replace(/\D/g, ""))} placeholder="Postal code (6 digits)" maxLength={6} className="bg-background border rounded-lg px-3 py-2 text-sm" />
                          </div>
                        </div>
                        <button onClick={handleRouteStart} disabled={routeBusy || !routePhone || !routeStreet1 || !routeCity || !routeState || routePostal.length !== 6 || (needsLegalPan && !routeLegalPan) || (needsLegalGst && !routeLegalGst)} className="w-full sm:w-auto px-6 py-2.5 rounded-xl bg-sky-600 text-white text-sm font-medium hover:bg-sky-700 disabled:opacity-50">{routeBusy ? "Working…" : "Create Payout Account"}</button>
                      </div>
                    )}
                    {(routeStatus?.onboarding_status==="STAKEHOLDER_PENDING"||routeStatus?.onboarding_status==="ACCOUNT_CREATED") && (
                      <div className="rounded-xl border p-5 space-y-3">
                        <h4 className="text-sm font-semibold">Business details</h4>
                        <p className="text-xs text-muted-foreground">Stakeholder identity is bound to your verified login email.</p>
                        <div className="grid sm:grid-cols-2 gap-3">
                          <input value={stakeName} onChange={e=>setStakeName(e.target.value)} placeholder="Full name (optional)" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                          <input value={stakePan} onChange={e=>setStakePan(e.target.value)} placeholder="PAN (optional)" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                        </div>
                        <button onClick={handleRouteStakeholder} disabled={routeBusy} className="w-full sm:w-auto text-sm px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">{routeBusy ? "Saving…" : "Save business details"}</button>
                      </div>
                    )}
                    {routeStatus?.onboarding_status==="PRODUCT_PENDING" && (
                      <div className="rounded-xl border p-5 space-y-3">
                        <h4 className="text-sm font-semibold">Route payouts product</h4>
                        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={routeTnc} onChange={e=>setRouteTnc(e.target.checked)} /> I accept the Razorpay Route terms for payouts</label>
                        <button onClick={handleRouteProduct} disabled={routeBusy || !routeTnc} className="w-full sm:w-auto text-sm px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">{routeBusy ? "Requesting…" : "Request payouts"}</button>
                      </div>
                    )}
                    {routeStatus?.onboarding_status==="BANK_DETAILS_PENDING" && (
                      <div className="rounded-xl border p-5 space-y-3">
                        <h4 className="text-sm font-semibold">Bank account for settlements</h4>
                        <p className="text-xs text-muted-foreground">Sent securely for verification. Only the last 4 digits are stored.</p>
                        <div className="grid sm:grid-cols-3 gap-3">
                          <input type="password" value={settleAcct} onChange={e=>setSettleAcct(e.target.value)} placeholder="Account number" autoComplete="off" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                          <input value={settleIfsc} onChange={e=>setSettleIfsc(e.target.value.toUpperCase())} placeholder="IFSC" autoComplete="off" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                          <input value={settleName} onChange={e=>setSettleName(e.target.value)} placeholder="Beneficiary name" className="bg-background border rounded-lg px-3 py-2 text-sm" />
                        </div>
                        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={routeTnc} onChange={e=>setRouteTnc(e.target.checked)} /> I accept the Razorpay Route terms for payouts</label>
                        <button onClick={handleRouteSettlement} disabled={routeBusy || !routeTnc || !settleAcct || !settleIfsc || !settleName} className="w-full sm:w-auto text-sm px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">{routeBusy ? "Submitting…" : "Submit bank details"}</button>
                      </div>
                    )}
                    {routeStatus?.onboarding_status==="UNDER_REVIEW" && (
                      <div className="rounded-xl border bg-amber-50 dark:bg-amber-950/20 p-5 border-amber-200">
                        <div className="flex items-center gap-2 text-sm font-medium text-amber-700"><Loader2 className="size-4 animate-spin"/> Bank account verification in progress.</div>
                        <button onClick={loadRouteStatus} className="mt-3 text-xs border px-3 py-1.5 rounded-lg hover:bg-accent">Refresh status</button>
                      </div>
                    )}
                    {(routeStatus?.transfer_ready || routeStatus?.bank_last4) && (
                      <div className="rounded-xl border bg-green-50 dark:bg-green-950/20 p-5 border-green-200">
                        <p className="text-sm font-semibold text-green-700">{routeStatus?.transfer_ready ? "Ready to receive payments" : "Bank account connected"}</p>
                        {routeStatus?.bank_last4 && <p className="text-xs text-muted-foreground mt-1">Account: •••• {routeStatus.bank_last4}{routeStatus?.beneficiary_name ? ` • ${routeStatus.beneficiary_name}` : ""}</p>}
                      </div>
                    )}
                    {(routeStatus?.onboarding_status==="SUSPENDED"||routeStatus?.onboarding_status==="FAILED") && (
                      <div className="rounded-xl border bg-red-50 dark:bg-red-950/20 p-5 border-red-200">
                        <p className="text-sm font-semibold text-red-700">Verification failed — action required</p>
                        <p className="text-xs text-muted-foreground mt-1 whitespace-pre-wrap">{routeStatus?.failure_reason || "Verification needs more information."}</p>
                      </div>
                    )}
                    {routeStatus && routeStatus.onboarding_status !== "NOT_STARTED" && (
                      <div className="rounded-xl border p-5 space-y-2">
                        <h4 className="text-sm font-semibold">Payment routing</h4>
                        <p className="text-xs text-muted-foreground">AUTO uses Route once payout-ready. ROUTE rejects new payments until payout-ready (no silent fallback).</p>
                        <div className="flex gap-2">
                          {(["AUTO","ROUTE"] as const).map(m => (
                            <button key={m} onClick={()=>handleRouteMode(m)} disabled={routeBusy || routeStatus.payment_mode===m} className={`text-xs px-3 py-1.5 rounded-lg border ${routeStatus.payment_mode===m ? "bg-primary text-primary-foreground" : "hover:bg-accent"} disabled:opacity-50`}>{m}</button>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="rounded-xl border p-5 space-y-3">
                      <h4 className="text-sm font-semibold">Appointment Price</h4>
                      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={routePriceEnabled} onChange={e=>setRoutePriceEnabled(e.target.checked)} /> Enable paid appointments</label>
                      {routePriceEnabled && (
                        <div className="flex gap-2 items-center">
                          <span className="text-sm">₹</span>
                          <input type="number" value={routePricePaise/100} onChange={e=>setRoutePricePaise(Math.round(parseFloat(e.target.value||"0")*100))} className="w-28 bg-background border rounded-lg px-3 py-2 text-sm" placeholder="500" />
                          <select value={routeCurrency} onChange={e=>setRouteCurrency(e.target.value)} className="bg-background border rounded-lg px-2 py-2 text-sm"><option value="INR">INR</option></select>
                        </div>
                      )}
                      <button onClick={handleSaveRoutePrice} disabled={routeSavingPrice} className="text-sm px-4 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50">{routeSavingPrice ? "Saving…" : "Save price"}</button>
                    </div>
                  </>
                )}
              </div>

              {/* ── Cashfree own-account collection ── */}
              <div className="mt-4">
                <PaymentsConnectCard />
              </div>

              {/* ── Recent transactions ── */}
              <div className="mt-4 bg-card border border-border rounded-2xl overflow-hidden shadow-sm">
                <div className="p-4 sm:p-5 border-b border-border/50">
                  <h3 className="font-semibold text-base leading-none">Recent transactions</h3>
                  <p className="text-xs text-muted-foreground mt-1">Every collection attempt, newest first.</p>
                </div>
                <div className="p-4 sm:p-5">
                  {payAttemptsLoading ? (
                    <p className="text-sm text-muted-foreground">Loading transactions…</p>
                  ) : payAttempts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No payments yet. They will appear here after the first booking payment.</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="border-b border-border text-left text-xs text-muted-foreground sm:text-left">
                            <th className="py-2 pr-3 sm:pr-0 font-medium whitespace-nowrap">Date</th>
                            <th className="py-2 pr-3 sm:pr-0 font-medium whitespace-nowrap">Provider</th>
                            <th className="py-2 pr-3 sm:pr-0 font-medium whitespace-nowrap">Amount</th>
                            <th className="py-2 pr-3 sm:pr-0 font-medium whitespace-nowrap">Status</th>
                            <th className="py-2 font-medium whitespace-nowrap">Order</th>
                          </tr>
                        </thead>
                        <tbody>
                          {payAttempts.map((a) => (
                            <tr key={a.id} className="border-b border-border/50 last:border-0">
                              <td className="py-2 pr-3 sm:pr-0 text-xs sm:whitespace-normal">{a.created_at ? new Date(a.created_at).toLocaleString() : "—"}</td>
                              <td className="py-2 pr-3 sm:pr-0 capitalize">{a.provider}</td>
                              <td className="py-2 pr-3 sm:pr-0 font-medium">₹{(Number(a.amount_paise) / 100).toFixed(2)}</td>
                              <td className="py-2 pr-3 sm:pr-0"><span className="rounded-full px-2 py-0.5 text-[11px] font-medium bg-zinc-500/15">{a.status}</span></td>
                              <td className="py-2 font-mono text-[11px] text-muted-foreground truncate max-w-[180px] sm:max-w-full">{a.order_id ?? "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {!payAttemptsLoading && payTotal > 0 && (
                    <div className="mt-3 flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-0 border-t border-border/50 pt-3">
                      <p className="text-xs text-muted-foreground sm:text-start">
                        Showing {payFrom}–{payTo} of {payTotal}
                      </p>
                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          disabled={payPage <= 1 || payAttemptsLoading}
                          onClick={() => loadPayAttempts(payPage - 1)}
                          className="text-xs sm:text-sm px-3 py-1.5 rounded-lg border border-border bg-background hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          Previous
                        </button>
                        <span className="text-xs sm:text-sm text-muted-foreground whitespace-nowrap">
                          Page {payPage} of {payTotalPages}
                        </span>
                        <button
                          type="button"
                          disabled={payPage >= payTotalPages || payAttemptsLoading}
                          onClick={() => loadPayAttempts(payPage + 1)}
                          className="text-xs sm:text-sm px-3 py-1.5 rounded-lg border border-border bg-background hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          Next
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          )}

          {activeTab === "whatsapp" && (
            <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-sm animate-in fade-in duration-500 slide-in-from-bottom-2">
              <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5 bg-secondary/40 border-b border-border/50">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-green-500/10 rounded-lg">
                    <MessageCircle className="size-5 text-green-500" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-base leading-none">WhatsApp Business Configuration</h3>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {(()=>{
                    if (qrStatus?.status === "CONNECTED") return <span className="text-xs font-bold uppercase tracking-wider bg-green-500/10 text-green-600 px-3 py-1 rounded-full">Connected</span>;
                    return <span className="text-xs font-bold uppercase tracking-wider bg-muted px-3 py-1 rounded-full text-muted-foreground">Not Connected</span>;
                  })()}
                  <button
                    onClick={() => { loadQrStatus(); }}
                    title="Refresh WhatsApp status"
                    aria-label="Refresh WhatsApp status"
                    className="relative inline-flex h-8 w-8 items-center justify-center rounded-full border border-border hover:bg-accent transition-colors"
                  >
                    <RefreshCw className="size-4" />
                  </button>
                </div>
              </div>

              {qrLoading ? (
                <div className="p-12 flex items-center justify-center">
                  <Loader2 className="size-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <div className="p-6 md:p-8 space-y-6">
                  <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-5">
                    <p className="text-sm font-semibold">Connect a dedicated business booking number</p>
                    <p className="text-xs text-muted-foreground mt-1">
                      Use a WhatsApp Business number reserved for customer bookings — not your personal number.
                      This uses an unofficial WhatsApp Web integration: Meta may throttle, disconnect, or restrict
                      numbers that look automated. Keep the number warmed up with normal activity.
                    </p>
                  </div>

                  {qrStatus?.status === "CONNECTED" ? (
                    <div className="rounded-xl border bg-green-50 dark:bg-green-950/20 p-5 border-green-200">
                      <div className="flex items-center gap-2 text-sm font-medium text-green-700">
                        <Check className="size-4" /> WhatsApp Connected
                        {qrStatus.phone_number ? <span className="font-mono">+{qrStatus.phone_number}</span> : null}
                      </div>
                      {qrStatus.display_name ? <p className="text-xs text-muted-foreground mt-1">{qrStatus.display_name}</p> : null}
                      <div className="mt-4 rounded-xl border border-border bg-background p-4">
                        <div className="flex items-center justify-between gap-3">
                          <div>
                            <p className="text-sm font-medium">Booking autoreplies</p>
                            <p className="text-xs text-muted-foreground mt-0.5">
                              {qrStatus.booking_active
                                ? "Active — customers messaging this number get booking replies."
                                : "Off — connected number stays silent until you enable this."}
                            </p>
                          </div>
                          <button
                            role="switch"
                            aria-checked={!!qrStatus.booking_enabled}
                            aria-label="Toggle WhatsApp booking autoreplies"
                            onClick={() => void toggleQrBooking(!qrStatus.booking_enabled)}
                            disabled={qrBusy}
                            className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors disabled:opacity-50 ${qrStatus.booking_enabled ? "bg-emerald-600" : "bg-muted"}`}
                          >
                            <span className={`inline-block size-4 transform rounded-full bg-white shadow transition-transform ${qrStatus.booking_enabled ? "translate-x-6" : "translate-x-1"}`} />
                          </button>
                        </div>
                      </div>
                      <div className="flex gap-2 mt-3">
                        <button onClick={reconnectQr} disabled={qrBusy} className="text-xs border px-3 py-1.5 rounded-lg hover:bg-accent disabled:opacity-50">Reconnect</button>
                        <button onClick={disconnectQr} disabled={qrBusy} className="text-xs border border-destructive/20 text-destructive px-3 py-1.5 rounded-lg hover:bg-destructive/10 disabled:opacity-50">Disconnect</button>
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-xl border border-dashed p-6 text-center bg-secondary/20 space-y-4">
                      <div className="flex justify-center"><QrCode className="size-8 text-muted-foreground" /></div>
                      <div>
                        <p className="text-sm font-medium">Link your business WhatsApp in under a minute</p>
                        <p className="text-xs text-muted-foreground mt-1">Click Connect, then on the business phone go to WhatsApp → Settings → Linked Devices → Link a Device and scan the code. QR refreshes automatically.</p>
                      </div>
                      {qrImg && (qrStatus?.status === "PENDING_QR" || qrStatus?.status === "DISCONNECTED") ? (
                        <>
                          <div className="flex justify-center">
                            <img src={qrImg.dataUrl} alt="WhatsApp pairing QR code" className="size-56 rounded-xl border border-border bg-white p-2" />
                          </div>
                          <p className="text-[11px] text-muted-foreground">Scan within a minute — the code refreshes automatically. If your phone says “couldn’t connect”, wait for the next fresh code, don’t retry the old one.</p>
                        </>
                      ) : qrWaiting ? (
                        <div className="flex flex-col items-center gap-2 py-6">
                          <Loader2 className="size-6 animate-spin text-muted-foreground" />
                          <p className="text-xs text-muted-foreground">Preparing a fresh QR code… keep this tab open.</p>
                        </div>
                      ) : null}
                      {qrStatus && qrStatus.status !== "NOT_CONNECTED" && qrStatus.status !== "LOGGED_OUT" ? (
                        <p className="text-xs text-muted-foreground">Status: {qrStatus.status}. Waiting for scan…</p>
                      ) : null}
                      <button onClick={startQrPairing} disabled={qrBusy} className="px-6 py-2.5 rounded-xl bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700 disabled:opacity-50">
                        {qrBusy ? <><Loader2 className="size-4 animate-spin inline mr-2" />Starting…</> : "Connect WhatsApp"}
                      </button>
                      {qrMsg && (
                        <p className={`text-xs ${qrMsg.type === "success" ? "text-green-600" : "text-red-600"}`}>{qrMsg.text}</p>
                      )}
                    </div>
                  )}

                </div>
              )}
            </div>
          )}

          {activeTab === "calendar" && (
            <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-sm animate-in fade-in duration-500 slide-in-from-bottom-2">
              <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5 bg-secondary/40 border-b border-border/50">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-indigo-500/10 rounded-lg">
                    <CalendarDays className="size-5 text-indigo-500" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-base leading-none">Calendar Integration</h3>
                  </div>
                </div>
                {activeProvider && activeProvider !== "NONE" && (
                  <span className="text-xs font-bold uppercase tracking-wider text-green-500 bg-green-500/10 px-3 py-1 rounded-full flex items-center gap-1.5">
                    <div className="size-1.5 bg-green-500 rounded-full animate-pulse" />
                    {activeProvider} Active
                  </span>
                )}
              </div>

              {(gcState.loading || ocState.loading) ? (
                <div className="p-12 flex items-center justify-center">
                  <Loader2 className="size-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <div className="p-6 md:p-8 space-y-8">
                  {migrationInfo && (
                    <div className="text-[13px] font-medium px-4 py-3 rounded-xl bg-blue-500/10 text-blue-600 border border-blue-500/20">
                      {migrationInfo}
                    </div>
                  )}

                  {/* Google Calendar Section */}
                  <div className="border border-border rounded-xl p-5">
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center gap-3">
                        <div className="bg-white p-1.5 rounded-lg border border-border shadow-sm">
                          <svg xmlns="http://w3.org/2000/svg" viewBox="0 0 48 48" width="20" height="20">
                            <path fill="#4285F4" d="M38 10H14c-2.2 0-4 1.8-4 4v4h28v-4c0-2.2-1.8-4-4-4z"/>
                            <path fill="#34A853" d="M38 14h4v24c0 2.2-1.8 4-4 4v-28z"/>
                            <path fill="#FBBC05" d="M38 38H14c-2.2 0-4-1.8-4-4v4c0 2.2 1.8 4 4 4h24c2.2 0 4-1.8 4-4z"/>
                            <path fill="#EA4335" d="M10 14H6v24c0 2.2 1.8 4 4 4v-28z"/>
                            <path fill="#FFF" d="M38 18H10v20h28V18z"/>
                            <path fill="#4285F4" d="M19.5 24.5h-3v2h3v5h2.5v-5h3v-2h-5.5zM29.5 24.5h-4.5v2h2.5c.6 0 1 .4 1 1s-.4 1-1 1h-2v2h2c1.4 0 2.5-1.1 2.5-2.5 0-.8-.4-1.5-1-2 .6-.4 1-1.1 1-1.9 0-1.4-1.1-2.6-2.5-2.6z"/>
                          </svg>
                        </div>
                        <div>
                          <h4 className="font-semibold text-sm">Google Calendar</h4>
                          <p className="text-xs text-muted-foreground">Sync bookings to Google Calendar</p>
                        </div>
                      </div>
                      {gcState.connected ? (
                        <span className="text-xs font-medium text-green-600 bg-green-500/10 px-2 py-1 rounded">Connected</span>
                      ) : (
                        <span className="text-xs font-medium text-muted-foreground px-2 py-1 rounded">Not connected</span>
                      )}
                    </div>
                    
                    {gcState.connected ? (
                      <div className="flex flex-col sm:flex-row gap-2">
                        <button
                          onClick={() => setActiveProviderHandler("GOOGLE")}
                          disabled={settingActiveProvider || activeProvider === "GOOGLE"}
                          className={`flex-1 text-sm font-medium px-4 py-2 rounded-lg transition-colors ${
                            activeProvider === "GOOGLE" 
                              ? "bg-green-500/20 text-green-600 border border-green-500/30"
                              : "bg-secondary border border-border hover:bg-accent"
                          }`}
                        >
                          {activeProvider === "GOOGLE" ? "✓ Active" : "Set as Active"}
                        </button>
                        <button
                          onClick={() => setConfirmAction("disconnect_google")}
                          disabled={gcState.disconnecting}
                          className="text-sm font-medium px-4 py-2 rounded-lg border border-destructive/20 text-destructive hover:bg-destructive/10"
                        >
                          {gcState.disconnecting ? <Loader2 className="size-4 animate-spin" /> : "Disconnect"}
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={connectGoogleCalendar}
                        disabled={gcState.connecting}
                        className="w-full text-sm font-medium px-4 py-2.5 rounded-lg bg-[#4285F4] text-white hover:bg-[#3367D6] transition-colors"
                      >
                        {gcState.connecting ? <Loader2 className="size-4 animate-spin inline" /> : "Connect Google Calendar"}
                      </button>
                    )}
                  </div>

                  {/* Outlook Calendar Section */}
                  <div className="border border-border rounded-xl p-5">
                    <div className="flex items-center justify-between mb-4">
                      <div className="flex items-center gap-3">
                        <div className="bg-white p-1.5 rounded-lg border border-border shadow-sm">
                          <svg xmlns="http://w3.org/2000/svg" viewBox="0 0 48 48" width="20" height="20">
                            <defs>
                              <linearGradient id="outlookBlue" x1="10%" y1="10%" x2="90%" y2="90%">
                                <stop offset="0%" stop-color="#00a1f1"/>
                                <stop offset="100%" stop-color="#0078d4"/>
                              </linearGradient>
                            </defs>
                            <rect x="12" y="12" width="30" height="30" rx="4" fill="url(#outlookBlue)"/>
                            <path fill="#005a9e" d="M12 12h30v7H12z" opacity="0.4"/>
                            <circle cx="18" cy="25" r="1.5" fill="#fff" opacity="0.8"/>
                            <circle cx="26" cy="25" r="1.5" fill="#fff" opacity="0.8"/>
                            <circle cx="34" cy="25" r="1.5" fill="#fff" opacity="0.8"/>
                            <circle cx="18" cy="33" r="1.5" fill="#fff" opacity="0.8"/>
                            <circle cx="26" cy="33" r="1.5" fill="#fff" opacity="0.8"/>
                            <circle cx="34" cy="33" r="1.5" fill="#fff" opacity="0.8"/>
                            <rect x="6" y="16" width="16" height="16" rx="3" fill="#0078d4"/>
                            <text x="14" y="29" fill="#fff" font-family="'Segoe UI', sans-serif" font-weight="bold" font-size="15" text-anchor="middle">O</text>
                          </svg>
                        </div>
                        <div>
                          <h4 className="font-semibold text-sm">Microsoft Outlook Calendar</h4>
                          <p className="text-xs text-muted-foreground">Sync bookings to Outlook Calendar</p>
                        </div>
                      </div>
                      {ocState.connected ? (
                        <span className="text-xs font-medium text-green-600 bg-green-500/10 px-2 py-1 rounded">Connected</span>
                      ) : (
                        <span className="text-xs font-medium text-muted-foreground px-2 py-1 rounded">Not connected</span>
                      )}
                    </div>
                    
                    {ocState.connected ? (
                      <div className="flex flex-col sm:flex-row gap-2">
                        <button
                          onClick={() => setActiveProviderHandler("OUTLOOK")}
                          disabled={settingActiveProvider || activeProvider === "OUTLOOK"}
                          className={`flex-1 text-sm font-medium px-4 py-2 rounded-lg transition-colors ${
                            activeProvider === "OUTLOOK" 
                              ? "bg-green-500/20 text-green-600 border border-green-500/30"
                              : "bg-secondary border border-border hover:bg-accent"
                          }`}
                        >
                          {activeProvider === "OUTLOOK" ? "✓ Active" : "Set as Active"}
                        </button>
                        <button
                          onClick={() => setConfirmAction("disconnect_outlook")}
                          disabled={ocState.disconnecting}
                          className="text-sm font-medium px-4 py-2 rounded-lg border border-destructive/20 text-destructive hover:bg-destructive/10"
                        >
                          {ocState.disconnecting ? <Loader2 className="size-4 animate-spin" /> : "Disconnect"}
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={connectOutlookCalendar}
                        disabled={ocState.connecting}
                        className="w-full text-sm font-medium px-4 py-2.5 rounded-lg bg-[#0078D4] text-white hover:bg-[#005a9e] transition-colors"
                      >
                        {ocState.connecting ? <Loader2 className="size-4 animate-spin inline" /> : "Connect Outlook Calendar"}
                      </button>
                    )}
                  </div>

                  {/* No Active Provider Option */}
                  {activeProvider && activeProvider !== "NONE" && (
                    <div className="pt-4 border-t border-border">
                      <button
                        onClick={() => setActiveProviderHandler("NONE")}
                        disabled={settingActiveProvider}
                        className="text-sm text-muted-foreground hover:text-foreground transition-colors"
                      >
                        Disable calendar sync entirely
                      </button>
                    </div>
                  )}
                </div>
              )}

              {/* Migration Confirmation Dialog */}
              <AlertDialog open={showMigrationDialog} onOpenChange={setShowMigrationDialog}>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Switch Calendar Provider?</AlertDialogTitle>
                    <AlertDialogDescription>
                      Switching from <strong>{activeProvider}</strong> to <strong>{pendingProvider}</strong> will trigger a background migration of all future bookings.
                      <br /><br />
                      Ensure your {pendingProvider} account is properly connected before confirming.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel onClick={handleMigrationCancel}>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={handleMigrationConfirm}>
                      Confirm Switch
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>

              <AlertDialog open={Boolean(confirmAction)} onOpenChange={(open) => !open && setConfirmAction(null)}>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>
                      {confirmAction === "regenerate_token" && "Regenerate Widget Token?"}
                      {confirmAction === "disconnect_google" && "Disconnect Google Calendar?"}
                      {confirmAction === "disconnect_outlook" && "Disconnect Outlook Calendar?"}
                    </AlertDialogTitle>
                    <AlertDialogDescription>
                      {confirmAction === "regenerate_token" &&
                        "Your existing embedded widget token will stop working immediately on all websites."}
                      {confirmAction === "disconnect_google" &&
                        "Google Calendar sync will stop immediately. Existing synced events will no longer be managed by this app."}
                      {confirmAction === "disconnect_outlook" &&
                        "Outlook Calendar sync will stop immediately. Existing synced events will no longer be managed by this app."}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel onClick={() => setConfirmAction(null)}>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={executeConfirmedAction}>
                      Confirm
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>

              <AlertDialog open={showMeetingPolicyWarning} onOpenChange={setShowMeetingPolicyWarning}>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Apply impactful meeting policy change?</AlertDialogTitle>
                    <AlertDialogDescription>
                      Switching from <strong>HYBRID</strong> to <strong>{pendingMeetingMode?.replace("_", " ")}</strong> can impact in-flight booking conversations.
                      Please confirm before applying this change.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel
                      onClick={() => {
                        setShowMeetingPolicyWarning(false);
                        setPendingMeetingMode(null);
                      }}
                    >
                      Cancel
                    </AlertDialogCancel>
                    <AlertDialogAction
                      onClick={() => {
                        if (pendingMeetingMode) {
                          setMeetingMode(pendingMeetingMode);
                        }
                        setShowMeetingPolicyWarning(false);
                        setPendingMeetingMode(null);
                      }}
                    >
                      Confirm
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          )}

          {activeTab === "providers" && (
            <div className="bg-card border border-border rounded-2xl overflow-hidden shadow-sm animate-in fade-in duration-500 slide-in-from-bottom-2">
              <div className="flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5 bg-secondary/40 border-b border-border/50">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-primary/10 rounded-lg">
                    <Video className="size-5 text-primary" />
                  </div>
                  <div>
                    <h3 className="font-semibold text-base leading-none">Meeting Providers</h3>
                    <p className="text-xs text-muted-foreground mt-1">Connect online meeting platforms for booking confirmations</p>
                  </div>
                </div>
                {meetingProviders?.active_meeting_provider && meetingProviders.active_meeting_provider !== "NONE" && (
                  <span className="text-xs font-bold uppercase tracking-wider text-green-500 bg-green-500/10 px-3 py-1 rounded-full flex items-center gap-1.5">
                    <div className="size-1.5 bg-green-500 rounded-full animate-pulse" />
                    {meetingProviders.active_meeting_provider.replace("_", " ")} Active
                  </span>
                )}
              </div>

              <div className="p-6 md:p-8 space-y-8">
                {meetingProvidersLoading ? (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground py-8">
                    <Loader2 className="size-4 animate-spin" />
                    Loading meeting providers...
                  </div>
                ) : (
                  <>
                    {/* Provider Cards */}
                    <div className="space-y-4">
                      {[
                        {
                          key: "GOOGLE_MEET" as const,
                          title: "Google Meet",
                          description: "Host meetings via Google Meet for online bookings.",
                          connected: Boolean(meetingProviders?.google_meet_connected),
                          iconBg: "bg-[#4285F4]",
                          icon: (
                            <img src="/google-meet-logo.png" alt="Google Meet" width="20" height="20" style={{ width: "20px", height: "20px" }} />
                          ),
                        },

                      ].map((provider) => {
                        const isActive = meetingProviders?.active_meeting_provider === provider.key;
                        const ui = meetingProviderUi[provider.key];
                        return (
                          <div key={provider.key} className="border border-border rounded-xl p-5">
                            <div className="flex items-center justify-between mb-3">
                              <div className="flex items-center gap-3">
                                <div className={`flex items-center justify-center size-10 rounded-xl shadow-sm ${provider.iconBg}`}>
                                  {provider.icon}
                                </div>
                                <div>
                                  <p className="text-sm font-semibold">{provider.title}</p>
                                  <p className="text-xs text-muted-foreground">{provider.description}</p>
                                </div>
                              </div>
                              <span
                                className={`text-[11px] px-2.5 py-1 rounded-full font-semibold ${
                                  provider.connected ? "bg-green-500/15 text-green-600" : "bg-muted text-muted-foreground"
                                }`}
                              >
                                {provider.connected ? "Connected" : "Not Connected"}
                              </span>
                            </div>

                            <div className="flex flex-wrap gap-2">
                              {provider.connected ? (
                                <>
                                  {isActive ? (
                                    <button
                                      onClick={() => handleActivateMeetingProvider("NONE")}
                                      disabled={meetingProviderSaving}
                                      className="text-sm font-medium px-4 py-2 rounded-lg border border-amber-500/20 text-amber-600 hover:bg-amber-500/10 disabled:opacity-50"
                                    >
                                      Disable
                                    </button>
                                  ) : (
                                    <button
                                      onClick={() => handleActivateMeetingProvider(provider.key)}
                                      disabled={meetingProviderSaving}
                                      className="text-sm font-medium px-4 py-2 rounded-lg bg-secondary border border-border hover:bg-accent disabled:opacity-50"
                                    >
                                      Set Active
                                    </button>
                                  )}
                                  <button
                                    onClick={() => handleMeetingProviderDisconnect(provider.key)}
                                    disabled={ui?.disconnecting}
                                    className="text-sm font-medium px-4 py-2 rounded-lg border border-destructive/20 text-destructive hover:bg-destructive/10 disabled:opacity-50"
                                  >
                                    {ui?.disconnecting ? "Disconnecting..." : "Disconnect"}
                                  </button>
                                </>
                              ) : (
                                <button
                                  onClick={() => handleMeetingProviderConnect(provider.key)}
                                  disabled={ui?.connecting}
                                  className="text-sm font-medium px-4 py-2.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                                >
                                  {ui?.connecting ? "Connecting..." : `Connect ${provider.title}`}
                                </button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    <div className="text-xs text-muted-foreground border border-amber-200/40 bg-amber-50/50 dark:bg-amber-950/20 dark:border-amber-900 rounded-lg p-3">
                      If a provider link is unavailable at confirmation time, customers are informed explicitly and asked to retry or contact support.
                    </div>

                  </>
                )}
              </div>
            </div>
          )}

        </div>
      </div>
    </div>
  );
}
