"use client";

import { useState, useEffect } from "react";
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

interface WhatsAppConfig {
  whatsapp_enabled: boolean;
  whatsapp_phone_number_id: string | null;
  whatsapp_business_account_id: string | null;
  whatsapp_verify_token: string | null;
  whatsapp_api_token_set: boolean;
  webhook_url: string;
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
  const [activeTab, setActiveTab] = useState<"overview" | "widget" | "whatsapp" | "calendar">("overview");
  const [copied, setCopied] = useState(false);
  const [publicApiBaseInput, setPublicApiBaseInput] = useState("");
  const [runtimeUrls, setRuntimeUrls] = useState<RuntimeUrls | null>(null);
  const { data: config } = useAgentConfig();

  // WhatsApp config state
  const [waConfig, setWaConfig] = useState<WhatsAppConfig | null>(null);
  const [waLoading, setWaLoading] = useState(true);
  const [waSaving, setWaSaving] = useState(false);
  const [waForm, setWaForm] = useState({
    whatsapp_enabled: false,
    whatsapp_phone_number_id: "",
    whatsapp_business_account_id: "",
    whatsapp_verify_token: "",
    whatsapp_api_token: "",
  });
  const [waMessage, setWaMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [waWebhookCopied, setWaWebhookCopied] = useState(false);

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

  useEffect(() => {
    loadWhatsAppConfig();
    loadCalendarConfig();
    loadWidgetToken();

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
      const data = await api.get<WidgetTokenData>("/dashboard/widget-token");
      setWidgetToken(data);
    } catch {
      // Token doesn't exist yet — backend will generate on first GET
    }
    setWtLoading(false);
  };

  const regenerateWidgetToken = async () => {
    setWtRegenerating(true);
    try {
      const data = await api.post<WidgetTokenData>("/dashboard/widget-token/regenerate");
      setWidgetToken(data);
      setWtRevealed(true);
      setTimeout(() => setWtRevealed(false), 3000);
      toast.success("Widget token regenerated.");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Failed to regenerate widget token");
    }
    setWtRegenerating(false);
  };

  const loadWhatsAppConfig = async () => {
    try {
      const data = await api.get<WhatsAppConfig>("/dashboard/whatsapp-config");
      setWaConfig(data);
      setWaForm({
        whatsapp_enabled: data.whatsapp_enabled,
        whatsapp_phone_number_id: data.whatsapp_phone_number_id || "",
        whatsapp_business_account_id: data.whatsapp_business_account_id || "",
        whatsapp_verify_token: data.whatsapp_verify_token || "",
        whatsapp_api_token: "",
      });
    } catch {
      // Not configured yet
    } finally {
      setWaLoading(false);
    }
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

  const saveWhatsAppConfig = async () => {
    setWaSaving(true);
    setWaMessage(null);
    try {
      const payload: Record<string, unknown> = {
        whatsapp_enabled: waForm.whatsapp_enabled,
        whatsapp_phone_number_id: waForm.whatsapp_phone_number_id || null,
        whatsapp_business_account_id: waForm.whatsapp_business_account_id || null,
        whatsapp_verify_token: waForm.whatsapp_verify_token || null,
      };
      if (waForm.whatsapp_api_token) {
        payload.whatsapp_api_token = waForm.whatsapp_api_token;
      }
      const data = await api.put<WhatsAppConfig>("/dashboard/whatsapp-config", payload);
      setWaConfig(data);
      setWaForm((prev) => ({ ...prev, whatsapp_api_token: "" }));
      setWaMessage({ type: "success", text: "WhatsApp configuration saved!" });
    } catch (err: unknown) {
      setWaMessage({ type: "error", text: err instanceof Error ? err.message : "Failed to save" });
    } finally {
      setWaSaving(false);
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
      forceNewSession: true
    };
    setTimeout(() => {
      const widget = document.querySelector('dotstark-booking-widget');
      if (widget && typeof widget._toggle === 'function') {
        widget._toggle();
      }
    }, 1000);
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
      status: waConfig?.whatsapp_enabled ? "Active" : "Configure",
      statusClass: waConfig?.whatsapp_enabled ? "bg-green-500/10 text-green-500" : "bg-yellow-500/10 text-yellow-500",
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
  ];

  const comingSoonIntegrations = [
    { title: "Microsoft Teams", icon: MessageSquare, iconBg: "bg-indigo-600" },
    { title: "Slack", icon: Hash, iconBg: "bg-purple-500" },
    { title: "HubSpot", icon: Link2, iconBg: "bg-orange-500" },
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
            {waConfig?.whatsapp_enabled && <ChevronRight className="size-4 opacity-50" />}
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
                      onClick={() => setActiveTab(item.id as "widget" | "whatsapp" | "calendar")}
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
                <h3 className="text-lg font-semibold tracking-tight">Meeting Providers</h3>
                <div className="bg-card border border-border rounded-xl p-5 space-y-5">
                  <p className="text-sm text-muted-foreground">
                    Manage meeting providers separately from calendar sync. Online confirmations can degrade gracefully when links are unavailable.
                  </p>

                  {meetingProvidersLoading ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="size-4 animate-spin" />
                      Loading meeting providers...
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {[
                        {
                          key: "GOOGLE_MEET" as const,
                          title: "Google Meet",
                          connected: Boolean(meetingProviders?.google_meet_connected),
                        },
                        {
                          key: "MICROSOFT_TEAMS" as const,
                          title: "Microsoft Teams",
                          connected: Boolean(meetingProviders?.microsoft_teams_connected),
                        },
                        {
                          key: "ZOOM" as const,
                          title: "Zoom",
                          connected: Boolean(meetingProviders?.zoom_connected),
                        },
                      ].map((provider) => {
                        const isActive = meetingProviders?.active_meeting_provider === provider.key;
                        const ui = meetingProviderUi[provider.key];
                        return (
                          <div key={provider.key} className="border border-border rounded-lg p-4">
                            <div className="flex items-center justify-between mb-3">
                        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                                <Video className="size-4 text-primary" />
                                <p className="text-sm font-semibold">{provider.title}</p>
                              </div>
                              <span
                                className={`text-[11px] px-2 py-1 rounded-full font-semibold ${
                                  provider.connected
                                    ? isActive
                                      ? "bg-green-500/15 text-green-600"
                                      : "bg-blue-500/10 text-blue-600"
                                    : "bg-amber-500/10 text-amber-600"
                                }`}
                              >
                                {provider.connected ? (isActive ? "Active" : "Connected") : "Not Connected"}
                              </span>
                            </div>

                            <div className="flex flex-wrap gap-2">
                              {provider.connected ? (
                                <>
                                  <button
                                    onClick={() => handleActivateMeetingProvider(provider.key)}
                                    disabled={meetingProviderSaving || isActive}
                                    className="text-sm px-3 py-2 rounded-lg border border-border hover:bg-accent disabled:opacity-50"
                                  >
                                    {isActive ? "✓ Active Provider" : "Set Active"}
                                  </button>
                                  <button
                                    onClick={() => handleMeetingProviderDisconnect(provider.key)}
                                    disabled={ui?.disconnecting}
                                    className="text-sm px-3 py-2 rounded-lg border border-destructive/20 text-destructive hover:bg-destructive/10 disabled:opacity-50"
                                  >
                                    {ui?.disconnecting ? "Disconnecting..." : "Disconnect"}
                                  </button>
                                </>
                              ) : (
                                <button
                                  onClick={() => handleMeetingProviderConnect(provider.key)}
                                  disabled={ui?.connecting}
                                  className="text-sm px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                                >
                                  {ui?.connecting ? "Connecting..." : `Connect ${provider.title}`}
                                </button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  <div className="text-xs text-muted-foreground border border-amber-200/40 bg-amber-50/50 dark:bg-amber-950/20 dark:border-amber-900 rounded-lg p-3">
                    If a provider link is unavailable at confirmation time, customers are informed explicitly and asked to retry or contact support.
                  </div>

                  {meetingProviders?.active_meeting_provider && meetingProviders.active_meeting_provider !== "NONE" && (
                    <button
                      onClick={() => handleActivateMeetingProvider("NONE")}
                      disabled={meetingProviderSaving}
                      className="text-sm text-muted-foreground hover:text-foreground"
                    >
                      Disable active meeting provider
                    </button>
                  )}
                </div>
              </div>

              <div className="space-y-4 pt-4 border-t border-border/50">
                <h3 className="text-lg font-semibold tracking-tight">Meeting Policy</h3>
                <div className="bg-card border border-border rounded-xl p-5 space-y-5">
                  <p className="text-sm text-muted-foreground">
                    Control which meeting types are allowed in booking flows. Changes are applied immediately across admin and widget surfaces.
                  </p>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="text-[13px] font-semibold text-foreground mb-1.5 block">Meeting Mode</label>
                      <select
                        title="Meeting mode"
                        value={meetingMode}
                        onChange={(e) => applyMeetingMode(e.target.value as "OFFLINE_ONLY" | "ONLINE_ONLY" | "HYBRID")}
                        className="w-full bg-background border border-border rounded-lg px-4 py-2.5 text-sm"
                      >
                        <option value="OFFLINE_ONLY">Offline only</option>
                        <option value="ONLINE_ONLY">Online only</option>
                        <option value="HYBRID">Hybrid</option>
                      </select>
                    </div>
                    <div>
                      <label className="text-[13px] font-semibold text-foreground mb-1.5 block">Default Online Provider</label>
                      <select
                        title="Meeting provider default"
                        value={meetingProviderDefault}
                        onChange={(e) => setMeetingProviderDefault(e.target.value as "NONE" | "GOOGLE_MEET" | "MICROSOFT_TEAMS" | "ZOOM")}
                        className="w-full bg-background border border-border rounded-lg px-4 py-2.5 text-sm"
                      >
                        <option value="NONE">None</option>
                        <option value="GOOGLE_MEET">Google Meet</option>
                        <option value="MICROSOFT_TEAMS">Microsoft Teams</option>
                        <option value="ZOOM">Zoom</option>
                      </select>
                    </div>
                  </div>

                  <div className="flex items-center justify-between border border-border rounded-lg px-4 py-3">
                    <div>
                      <p className="text-sm font-medium">Allow hybrid user selection</p>
                      <p className="text-xs text-muted-foreground">
                        When disabled in HYBRID mode, booking flow is restricted to a single deterministic option.
                      </p>
                    </div>
                    <button
                      onClick={() => setAllowHybridSelection((v) => !v)}
                      title="Toggle hybrid option selection"
                      aria-label="Toggle hybrid option selection"
                      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                        allowHybridSelection ? "bg-green-500" : "bg-muted"
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
                          allowHybridSelection ? "translate-x-6" : "translate-x-1"
                        }`}
                      />
                    </button>
                  </div>

                  <div className="text-xs text-muted-foreground">
                    Available in widget:{" "}
                    <span className="font-medium text-foreground">
                      {meetingMode === "ONLINE_ONLY"
                        ? "ONLINE"
                        : meetingMode === "OFFLINE_ONLY"
                          ? "OFFLINE"
                          : allowHybridSelection
                            ? "ONLINE + OFFLINE"
                            : "OFFLINE"}
                    </span>
                  </div>

                  <div className="pt-2 border-t border-border/50 flex justify-end">
                    <button
                      onClick={saveMeetingPolicy}
                      disabled={policySaving}
                      className="flex items-center gap-2 text-sm font-medium px-6 py-2.5 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm"
                    >
                      {policySaving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                      Save Meeting Policy
                    </button>
                  </div>
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
                      onClick={() => setConfirmAction("regenerate_token")}
                      disabled={wtRegenerating}
                      className="flex items-center gap-2 text-[12px] font-medium px-3 py-1.5 rounded-md border border-amber-200 dark:border-amber-800 text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950 transition-colors disabled:opacity-50"
                    >
                      <Loader2 className={`size-3.5 ${wtRegenerating ? "animate-spin" : ""}`} />
                      {wtRegenerating ? "Generating..." : "Regenerate"}
                    </button>
                  )}
                </div>
                <div className="p-6 md:p-8">
                  <p className="text-sm text-muted-foreground mb-6 leading-relaxed">
                    A <strong>widget token</strong> is a limited-purpose key used to embed the chat widget on your website. 
                    It cannot access your dashboard, modify bookings, or view sensitive data.
                  </p>
                  
                  {wtLoading ? (
                    <div className="flex items-center gap-3">
                      <Loader2 className="size-4 animate-spin text-muted-foreground" />
                      <span className="text-sm text-muted-foreground">Loading token...</span>
                    </div>
                  ) : widgetToken ? (
                    <div className="space-y-5">
                      <div className="bg-green-500/5 border border-green-500/20 rounded-xl p-4 flex items-start gap-3">
                        <Shield className="size-5 text-green-500 shrink-0 mt-0.5" />
                        <div>
                          <p className="text-sm font-semibold text-green-600 dark:text-green-400">
                            Widget token is active
                          </p>
                          <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                            It is completely safe to embed this in your public HTML.
                          </p>
                        </div>
                      </div>
                      <div>
                        <label className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-2 block">
                          Your Active Token
                        </label>
                      <div className="flex flex-col sm:flex-row sm:items-center gap-2">
                          <div className="flex-1 flex items-center gap-3 bg-secondary/30 border border-border rounded-xl px-4 py-3">
                            <Code2 className="size-4 text-muted-foreground shrink-0" />
                            <code className="text-sm font-mono text-foreground break-all tracking-tight">
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

              <div className="bg-card border border-border rounded-2xl p-6 space-y-4 shadow-sm">
                <h3 className="font-semibold text-base">Public URL Configuration</h3>
                <p className="text-sm text-muted-foreground">
                  Set the public API base used in embed snippets (Cloudflare backend URL + <code>/api/v1</code>).
                </p>
                <div className="flex flex-col md:flex-row gap-3">
                  <input
                    type="text"
                    value={publicApiBaseInput}
                    onChange={(e) => setPublicApiBaseInput(e.target.value)}
                    placeholder={`${runtimeApiBase}`}
                    className="flex-1 bg-background border border-border rounded-lg px-4 py-2.5 text-sm"
                  />
                  <button
                    onClick={() => persistPublicApiBase(publicApiBaseInput)}
                    disabled={!publicApiBaseInput.trim()}
                    className="text-sm font-medium px-4 py-2.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    Save URL
                  </button>
                  <button
                    onClick={() => persistPublicApiBase(runtimeApiBase)}
                    className="text-sm font-medium px-4 py-2.5 rounded-lg border border-border hover:bg-accent"
                  >
                    Use Current Runtime
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Script source: <code>{selectedPublicApiRoot}/static/widget/widget.js</code>
                </p>
                {oauthWarnings.length > 0 && (
                  <div className="space-y-2 text-xs text-amber-700 dark:text-amber-300 bg-amber-50/70 dark:bg-amber-950/20 border border-amber-200/60 dark:border-amber-900 rounded-lg p-3">
                    {oauthWarnings.map((warning) => (
                      <p key={warning}>• {warning}</p>
                    ))}
                  </div>
                )}
                {Object.keys(oauthRedirectUris).length > 0 && (
                  <div className="text-xs text-muted-foreground space-y-1">
                    <p className="font-semibold text-foreground">OAuth Redirect URIs</p>
                    <p>Google: <code>{oauthRedirectUris.google}</code></p>
                    <p>Microsoft: <code>{oauthRedirectUris.microsoft}</code></p>
                    <p>Zoom: <code>{oauthRedirectUris.zoom}</code></p>
                  </div>
                )}
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
                  <span className="text-sm font-medium text-muted-foreground">
                    {waForm.whatsapp_enabled ? "Enabled" : "Disabled"}
                  </span>
                  <button
                    onClick={() => setWaForm((f) => ({ ...f, whatsapp_enabled: !f.whatsapp_enabled }))}
                    title="Toggle WhatsApp Integration"
                    aria-label="Toggle WhatsApp Integration"
                    className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                      waForm.whatsapp_enabled ? "bg-green-500" : "bg-muted"
                    }`}
                  >
                    <span
                      className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${
                        waForm.whatsapp_enabled ? "translate-x-6" : "translate-x-1"
                      }`}
                    />
                  </button>
                </div>
              </div>

              {waLoading ? (
                <div className="p-12 flex items-center justify-center">
                  <Loader2 className="size-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <div className="p-6 md:p-8 space-y-6">
                  {waConfig?.webhook_url && (
                    <div className="p-5 bg-secondary/20 rounded-xl border border-dashed border-border">
                      <label className="text-xs font-semibold text-muted-foreground uppercase tracking-widest mb-2 block">
                        Your Meta Webhook URL
                      </label>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 flex items-center gap-3 bg-background border border-border rounded-lg px-4 py-2.5">
                          <Link2 className="size-4 text-muted-foreground shrink-0" />
                          <code className="text-[13px] font-mono text-foreground truncate">{waConfig.webhook_url}</code>
                        </div>
                        <button
                          onClick={() => {
                            navigator.clipboard.writeText(waConfig.webhook_url);
                            setWaWebhookCopied(true);
                            setTimeout(() => setWaWebhookCopied(false), 2000);
                          }}
                          className="flex items-center gap-2 text-[13px] font-medium px-4 py-2.5 rounded-lg bg-secondary border border-border hover:bg-accent transition-colors"
                        >
                          {waWebhookCopied ? <Check className="size-4 text-green-500" /> : <Copy className="size-4" />}
                          {waWebhookCopied ? "Copied!" : "Copy URL"}
                        </button>
                      </div>
                    </div>
                  )}

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4">
                    <div className="space-y-4 md:col-span-2">
                      <h4 className="text-sm font-semibold border-b border-border/50 pb-2">Meta Credentials</h4>
                    </div>
                    <div>
                      <label className="text-[13px] font-semibold text-foreground mb-1.5 block">Phone Number ID</label>
                      <input
                        type="text"
                        title="Phone Number ID"
                        placeholder="Enter Phone Number ID"
                        value={waForm.whatsapp_phone_number_id}
                        onChange={(e) => setWaForm((f) => ({ ...f, whatsapp_phone_number_id: e.target.value }))}
                        className="w-full bg-background border border-border rounded-lg px-4 py-2.5 text-sm"
                      />
                    </div>
                    <div>
                      <label className="text-[13px] font-semibold text-foreground mb-1.5 block">Business Account ID</label>
                      <input
                        type="text"
                        title="Business Account ID"
                        placeholder="Enter Business Account ID"
                        value={waForm.whatsapp_business_account_id}
                        onChange={(e) => setWaForm((f) => ({ ...f, whatsapp_business_account_id: e.target.value }))}
                        className="w-full bg-background border border-border rounded-lg px-4 py-2.5 text-sm"
                      />
                    </div>
                    
                    <div className="space-y-4 md:col-span-2 mt-2">
                      <h4 className="text-sm font-semibold border-b border-border/50 pb-2">Authentication</h4>
                    </div>
                    <div>
                      <label className="text-[13px] font-semibold text-foreground mb-1.5 block">Verify Token</label>
                      <input
                        type="text"
                        title="Verify Token"
                        placeholder="Enter Verify Token"
                        value={waForm.whatsapp_verify_token}
                        onChange={(e) => setWaForm((f) => ({ ...f, whatsapp_verify_token: e.target.value }))}
                        className="w-full bg-background border border-border rounded-lg px-4 py-2.5 text-sm"
                      />
                    </div>
                    <div>
                      <label className="text-[13px] font-semibold text-foreground mb-1.5 block">Permanent API Token</label>
                      <div className="relative">
                        <input
                          type="password"
                          value={waForm.whatsapp_api_token}
                          onChange={(e) => setWaForm((f) => ({ ...f, whatsapp_api_token: e.target.value }))}
                          placeholder={waConfig?.whatsapp_api_token_set ? "•••••••••••• (Saved)" : ""}
                          className="w-full bg-background border border-border rounded-lg px-4 py-2.5 text-sm pr-10"
                        />
                        <Shield className="absolute right-3 top-3 size-4 text-muted-foreground" />
                      </div>
                    </div>
                  </div>

                  {waMessage && (
                    <div
                      className={`text-[13px] font-medium px-4 py-3 rounded-xl flex items-center gap-2 mt-4 ${
                        waMessage.type === "success" ? "bg-green-500/10 text-green-600 dark:text-green-400 border border-green-500/20" : "bg-red-500/10 text-red-600 dark:text-red-400 border border-red-500/20"
                      }`}
                    >
                      {waMessage.type === "success" ? <Check className="size-4" /> : <AlertCircle className="size-4" />}
                      {waMessage.text}
                    </div>
                  )}

                  <div className="pt-6 mt-4 border-t border-border/50 flex justify-end">
                    <button
                      onClick={saveWhatsAppConfig}
                      disabled={waSaving}
                      className="flex items-center gap-2 text-sm font-medium px-6 py-2.5 rounded-xl bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm"
                    >
                      {waSaving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
                      Save Configuration
                    </button>
                  </div>
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
                        <div className="bg-[#4285F4] p-2 rounded-lg">
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="white">
                            <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
                            <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
                            <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
                            <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
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
                        <div className="bg-[#0078D4] p-2 rounded-lg">
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="white">
                            <path d="M24 12.2c0-1.2-.2-2.4-.7-3.5l-7.8-7.8c-1.4-1.4-3.2-2.2-5.1-2.2s-3.7.8-5.1 2.2L0 8.3c0 1.2.2 2.4.7 3.5l7.8 7.8c.5-.9 1.2-1.7 2-2.4.8-.7 1.8-1.2 2.9-1.5-.3-.4-.5-.9-.5-1.4 0-1.3 1.1-2.4 2.4-2.4s2.4 1.1 2.4 2.4c0 .5-.2 1-.5 1.4 1.1.3 2.1.8 2.9 1.5.8.7 1.5 1.5 2 2.4l7.8-7.8c-.5-1.1-.7-2.3-.7-3.5z"/>
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

        </div>
      </div>
    </div>
  );
}
