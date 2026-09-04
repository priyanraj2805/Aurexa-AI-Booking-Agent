"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { useRouter } from "next/navigation";
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
import {
  Loader2, AlertCircle, Save, Bot, Sparkles,
  Plus, GripVertical, Trash2, ChevronDown, ChevronUp,
  ShieldCheck, CalendarDays, Eye, EyeOff, Copy, Check, KeyRound,
  Upload, Image as ImageIcon, X, Link as LinkIcon,
} from "lucide-react";
import { useAgentConfig, type PersonaConfig, type FieldDefinition, type FieldType } from "@/hooks/use-dashboard";
import { api, uploadFile } from "@/lib/api";
import { toast } from "sonner";
import { KnowledgeBaseSection } from "./components/knowledge-base";

const TONE_OPTIONS = [
  { value: "professional", label: "Professional" },
  { value: "friendly", label: "Friendly" },
  { value: "casual", label: "Casual" },
  { value: "formal", label: "Formal" },
  { value: "empathetic", label: "Empathetic" },
];

const FIELD_TYPE_OPTIONS: { value: FieldType; label: string; icon: string }[] = [
  { value: "text", label: "Text", icon: "📝" },
  { value: "email", label: "Email", icon: "📧" },
  { value: "phone", label: "Phone", icon: "📞" },
  { value: "date", label: "Date", icon: "📅" },
  { value: "time", label: "Time", icon: "⏰" },
  { value: "select", label: "Select / Dropdown", icon: "📋" },
  { value: "number", label: "Number", icon: "🔢" },
  { value: "textarea", label: "Long Text", icon: "📄" },
];

const DEFAULT_ICONS: Record<string, string> = {
  text: "📝", email: "📧", phone: "📞", date: "📅",
  time: "⏰", select: "📋", number: "🔢", textarea: "📄",
};

function makeEmptyField(order: number): FieldDefinition {
  return {
    name: "",
    type: "text",
    label: "",
    required: true,
    placeholder: "",
    description: "",
    icon: "📝",
    options: [],
    order,
  };
}

// ── Field Row Component ────────────────────────────────────
function FieldRow({
  field,
  index,
  expanded,
  onToggle,
  onChange,
  onDelete,
  onMoveUp,
  onMoveDown,
  isFirst,
  isLast,
}: {
  field: FieldDefinition;
  index: number;
  expanded: boolean;
  onToggle: () => void;
  onChange: (updated: FieldDefinition) => void;
  onDelete: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  isFirst: boolean;
  isLast: boolean;
}) {
  const [optionInput, setOptionInput] = useState("");

  const update = (patch: Partial<FieldDefinition>) => {
    onChange({ ...field, ...patch });
  };

  const addOption = () => {
    const trimmed = optionInput.trim();
    if (trimmed && !field.options.includes(trimmed)) {
      update({ options: [...field.options, trimmed] });
      setOptionInput("");
    }
  };

  const removeOption = (opt: string) => {
    update({ options: field.options.filter((o) => o !== opt) });
  };

  return (
    <div className="border border-border rounded-lg bg-background overflow-hidden">
      {/* Collapsed row */}
      <div
        className="flex items-center gap-2 sm:gap-3 px-3 sm:px-4 py-3 cursor-pointer hover:bg-accent/30 transition-colors"
        onClick={onToggle}
      >
        <div className="flex flex-col gap-0.5">
          <button
            onClick={(e) => { e.stopPropagation(); onMoveUp(); }}
            disabled={isFirst}
            className="text-muted-foreground hover:text-foreground disabled:opacity-20 transition-colors"
          >
            <ChevronUp className="size-3.5" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onMoveDown(); }}
            disabled={isLast}
            className="text-muted-foreground hover:text-foreground disabled:opacity-20 transition-colors"
          >
            <ChevronDown className="size-3.5" />
          </button>
        </div>

        <span className="text-lg">{field.icon || DEFAULT_ICONS[field.type]}</span>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm truncate">
              {field.label || field.name || "(Unnamed field)"}
            </span>
            <span className="text-[11px] font-mono bg-secondary/50 px-1.5 py-0.5 rounded text-muted-foreground">
              {field.type}
            </span>
            {field.required && (
              <span className="text-[11px] bg-primary/10 text-primary px-1.5 py-0.5 rounded font-medium">
                Required
              </span>
            )}
          </div>
          {field.name && (
            <p className="text-[11px] text-muted-foreground/60 font-mono mt-0.5">
              key: {field.name}
            </p>
          )}
        </div>

        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="text-muted-foreground hover:text-red-500 transition-colors p-1"
        >
          <Trash2 className="size-4" />
        </button>

        <ChevronDown
          className={`size-4 text-muted-foreground transition-transform ${expanded ? "rotate-180" : ""}`}
        />
      </div>

      {/* Expanded editor */}
      {expanded && (
        <div className="border-t border-border px-3 sm:px-4 py-4 space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="text-[12px] text-muted-foreground font-medium mb-1.5 block">
                Field Key <span className="text-red-400">*</span>
              </label>
              <input
                type="text"
                value={field.name}
                onChange={(e) => update({ name: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_") })}
                placeholder="e.g. company_name"
                className="w-full bg-card border border-border text-foreground px-3 py-2 rounded-md text-[13px] font-mono focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              />
            </div>
            <div>
              <label className="text-[12px] text-muted-foreground font-medium mb-1.5 block">
                Display Label <span className="text-red-400">*</span>
              </label>
              <input
                type="text"
                value={field.label}
                onChange={(e) => update({ label: e.target.value })}
                placeholder="e.g. Company Name"
                className="w-full bg-card border border-border text-foreground px-3 py-2 rounded-md text-[13px] focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              />
            </div>
            <div>
              <label className="text-[12px] text-muted-foreground font-medium mb-1.5 block">Field Type</label>
              <select
                value={field.type}
                onChange={(e) => {
                  const newType = e.target.value as FieldType;
                  update({ type: newType, icon: DEFAULT_ICONS[newType] || "📋" });
                }}
                className="w-full bg-card border border-border text-foreground px-3 py-2 rounded-md text-[13px] focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              >
                {FIELD_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.icon} {opt.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-[12px] text-muted-foreground font-medium mb-1.5 block">Placeholder</label>
              <input
                type="text"
                value={field.placeholder}
                onChange={(e) => update({ placeholder: e.target.value })}
                placeholder="Hint text shown in input"
                className="w-full bg-card border border-border text-foreground px-3 py-2 rounded-md text-[13px] focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              />
            </div>
            <div>
              <label className="text-[12px] text-muted-foreground font-medium mb-1.5 block">
                Description (for AI context)
              </label>
              <input
                type="text"
                value={field.description}
                onChange={(e) => update({ description: e.target.value })}
                placeholder="Help the AI understand this field"
                className="w-full bg-card border border-border text-foreground px-3 py-2 rounded-md text-[13px] focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              />
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-4 sm:gap-6">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={field.required}
                onChange={(e) => update({ required: e.target.checked })}
                className="size-4 rounded border-border accent-primary"
              />
              <span className="text-[13px] font-medium">Required field</span>
            </label>
            <div className="flex items-center gap-2">
              <label className="text-[12px] text-muted-foreground font-medium">Icon</label>
              <input
                type="text"
                value={field.icon}
                onChange={(e) => update({ icon: e.target.value })}
                className="w-12 text-center bg-card border border-border text-foreground px-1 py-1 rounded-md text-base focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
                maxLength={4}
              />
            </div>
          </div>

          {/* Select options editor */}
          {field.type === "select" && (
            <div>
              <label className="text-[12px] text-muted-foreground font-medium mb-1.5 block">
                Dropdown Options
              </label>
              <div className="flex items-center gap-2 mb-2">
                <input
                  type="text"
                  value={optionInput}
                  onChange={(e) => setOptionInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addOption()}
                  placeholder="Type option and press Enter"
                  className="flex-1 bg-card border border-border text-foreground px-3 py-1.5 rounded-md text-[13px] focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
                />
                <button
                  onClick={addOption}
                  className="text-[12px] font-medium px-3 py-1.5 rounded-md bg-secondary border border-border text-foreground hover:bg-accent transition-colors"
                >
                  Add
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {field.options.map((opt) => (
                  <span
                    key={opt}
                    className="inline-flex items-center gap-1 text-[12px] bg-secondary/50 border border-border px-2 py-1 rounded-md"
                  >
                    {opt}
                    <button
                      onClick={() => removeOption(opt)}
                      className="text-muted-foreground hover:text-red-500 transition-colors"
                    >
                      ✕
                    </button>
                  </span>
                ))}
                {field.options.length === 0 && (
                  <span className="text-[12px] text-muted-foreground/60 italic">No options added yet</span>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main Page Component ────────────────────────────────────
export default function AgentCorePage() {
  const { data: config, loading, error, refetch } = useAgentConfig();
  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  // Organization settings
  const [brandColor, setBrandColor] = useState("#3B82F6");
  const [businessName, setBusinessName] = useState("");
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const [logoUploading, setLogoUploading] = useState(false);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [logoUrlInput, setLogoUrlInput] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Persona state
  const [persona, setPersona] = useState<PersonaConfig>({
    agent_name: "",
    greeting_message: "",
    tone: "professional",
    industry_context: "",
    custom_instructions: "",
    business_name: "",
    business_tagline: "",
  });

  // Rich field definitions state
  const [fieldDefs, setFieldDefs] = useState<FieldDefinition[]>([]);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  // Track whether booking fields were modified — the backend validates
  // field_definitions strictly, so we only send them when actually changed.
  const fieldsDirtyRef = useRef(false);

  // Org API key (full value fetched on demand — only masked preview from agent-config)
  const [apiKeyFull, setApiKeyFull] = useState<string | null>(null);
  const [apiKeyLoading, setApiKeyLoading] = useState(false);
  const [apiKeyRevealed, setApiKeyRevealed] = useState(false);
  const [apiKeyCopied, setApiKeyCopied] = useState(false);
  const [apiKeyRegenerating, setApiKeyRegenerating] = useState(false);
  const [apiKeyError, setApiKeyError] = useState<string | null>(null);

  const toggleApiKeyVisibility = async () => {
    if (apiKeyRevealed) {
      setApiKeyRevealed(false);
      return;
    }
    if (apiKeyFull !== null) {
      setApiKeyRevealed(true);
      return;
    }
    setApiKeyLoading(true);
    setApiKeyError(null);
    try {
      const data = await api.get<{ api_key: string }>("/dashboard/api-key");
      setApiKeyFull(data?.api_key || "");
      setApiKeyRevealed(true);
    } catch (err) {
      setApiKeyError(err instanceof Error ? err.message : "Failed to load API key");
    } finally {
      setApiKeyLoading(false);
    }
  };

  const copyApiKey = async () => {
    try {
      if (apiKeyFull === null) {
        const data = await api.get<{ api_key: string }>("/dashboard/api-key");
        setApiKeyFull(data?.api_key || "");
      }
      await navigator.clipboard.writeText(apiKeyFull || "");
      setApiKeyCopied(true);
      setTimeout(() => setApiKeyCopied(false), 2000);
    } catch (err) {
      setApiKeyError(err instanceof Error ? err.message : "Failed to copy API key");
    }
  };

  const regenerateApiKey = async () => {
    if (!window.confirm("Rotating the API key moves the current key to a 7-day grace period and issues a new one. Existing integrations keep working for 7 days. Continue?")) {
      return;
    }
    setApiKeyRegenerating(true);
    setApiKeyError(null);
    try {
      const data = await api.post<{ api_key: string }>("/dashboard/api-key/regenerate");
      setApiKeyFull(data?.api_key || "");
      setApiKeyRevealed(true);
    } catch (err) {
      setApiKeyError(err instanceof Error ? err.message : "Failed to regenerate API key");
    } finally {
      setApiKeyRegenerating(false);
    }
  };

  const handleLogoFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setLogoError("Please select an image file");
      return;
    }
    if (file.size > 2 * 1024 * 1024) {
      setLogoError("Image must be under 2MB");
      return;
    }
    setLogoUploading(true);
    setLogoError(null);
    try {
      const res = await uploadFile<{ logo_url: string }>("/dashboard/agent/logo", file);
      setLogoUrl(res.logo_url);
      setLogoUrlInput(res.logo_url);
      toast.success("Logo uploaded");
      await refetch();
    } catch (err) {
      setLogoError(err instanceof Error ? err.message : "Upload failed");
      toast.error(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setLogoUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleLogoUrlSave = async () => {
    const url = logoUrlInput.trim();
    if (!url) {
      setLogoError("Please enter an image URL");
      return;
    }
    try {
      new URL(url);
    } catch {
      setLogoError("Invalid URL");
      return;
    }
    if (!/\.(png|jpe?g|webp|svg)(\?.*)?$/i.test(url) && !url.startsWith("/api")) {
      // allow any image url, just warn
    }
    setLogoUploading(true);
    setLogoError(null);
    try {
      await api.put("/dashboard/agent-config", { logo_url: url });
      setLogoUrl(url);
      toast.success("Logo URL saved");
      await refetch();
    } catch (err) {
      setLogoError(err instanceof Error ? err.message : "Failed to save URL");
    } finally {
      setLogoUploading(false);
    }
  };

  const handleLogoRemove = async () => {
    setLogoUploading(true);
    setLogoError(null);
    try {
      await api.del("/dashboard/agent/logo");
      // also clear via config update to keep DB consistent
      try { await api.put("/dashboard/agent-config", { logo_url: "" }); } catch {}
      setLogoUrl(null);
      setLogoUrlInput("");
      toast.success("Logo removed");
      await refetch();
    } catch (err) {
      setLogoError(err instanceof Error ? err.message : "Failed to remove");
    } finally {
      setLogoUploading(false);
    }
  };

  // Booking settings state
  const [requireOtp, setRequireOtp] = useState(true);
  const [requirePhoneOtp, setRequirePhoneOtp] = useState(false);
  const [meetingMode, setMeetingMode] = useState<"OFFLINE_ONLY" | "ONLINE_ONLY" | "HYBRID">("OFFLINE_ONLY");
  const [allowHybridSelection, setAllowHybridSelection] = useState(true);
  const [meetingProviderDefault, setMeetingProviderDefault] = useState<"NONE" | "GOOGLE_MEET">("NONE");

  // Sync config → local state when loaded
  useEffect(() => {
    if (config) {
      setBrandColor(config.ui_brand_color || "#3B82F6");
      setBusinessName(config.business_name || "");
      setRequireOtp(config.require_otp ?? true);
      setRequirePhoneOtp((config as unknown as { require_phone_otp?: boolean }).require_phone_otp ?? false);
      const mp = (config as unknown as { meeting_policy?: { meeting_mode?: string; allow_hybrid_selection?: boolean; meeting_provider_default?: string } }).meeting_policy;
      if (mp) {
        setMeetingMode((mp.meeting_mode as "OFFLINE_ONLY" | "ONLINE_ONLY" | "HYBRID") || "OFFLINE_ONLY");
        setAllowHybridSelection(mp.allow_hybrid_selection ?? true);
        const prov = (mp.meeting_provider_default as "NONE" | "GOOGLE_MEET") || "NONE";
        setMeetingProviderDefault(prov === "GOOGLE_MEET" ? "GOOGLE_MEET" : "NONE");
      }
      setLogoUrl((config as unknown as { logo_url?: string | null }).logo_url || null);
      setLogoUrlInput((config as unknown as { logo_url?: string | null }).logo_url || "");
      if (config.custom_prompts && typeof config.custom_prompts === "object") {
        const rawTone = String(config.custom_prompts.tone || "professional").toLowerCase();
        let normTone = config.custom_prompts.tone || "professional";
        if (rawTone.includes("casual") || rawTone.includes("warm")) normTone = "casual";
        else if (rawTone.includes("formal") || rawTone.includes("polite")) normTone = "formal";
        else if (rawTone.includes("friendly")) normTone = "friendly";
        else if (rawTone.includes("empathetic")) normTone = "empathetic";
        else if (rawTone.includes("concise") || rawTone.includes("direct")) normTone = "professional";
        setPersona({
          agent_name: config.custom_prompts.agent_name || "",
          greeting_message: config.custom_prompts.greeting_message || "",
          tone: normTone,
          industry_context: config.custom_prompts.industry_context || "",
          custom_instructions: config.custom_prompts.custom_instructions || "",
          business_name: config.custom_prompts.business_name || "",
          business_tagline: config.custom_prompts.business_tagline || "",
        });
      }
      // Load field definitions (or convert legacy flat fields)
      if (config.field_definitions && config.field_definitions.length > 0) {
        setFieldDefs(config.field_definitions);
      } else if (config.required_fields && config.required_fields.length > 0) {
        // Legacy: convert flat strings to basic definitions
        const presets: Record<string, Partial<FieldDefinition>> = {
          name: { type: "text", label: "Full Name", placeholder: "Your full name", icon: "👤" },
          email: { type: "email", label: "Email Address", placeholder: "you@example.com", icon: "📧" },
          phone: { type: "phone", label: "Phone Number", placeholder: "+1 (555) 000-0000", icon: "📞" },
          date: { type: "date", label: "Preferred Date", placeholder: "Select a date", icon: "📅" },
          time: { type: "time", label: "Preferred Time", placeholder: "Select a time slot", icon: "⏰" },
        };
        const converted: FieldDefinition[] = config.required_fields.filter(Boolean).map((name: string, i: number) => ({
          name: String(name),
          type: (presets[name]?.type || "text") as FieldType,
          label: presets[name]?.label || String(name).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
          required: true,
          placeholder: presets[name]?.placeholder || "",
          description: "",
          icon: presets[name]?.icon || "📋",
          options: [],
          order: i,
        }));
        setFieldDefs(converted);
      }
    }
  }, [config]);

  // ── Unsaved changes guard ────────────────────────
  const router = useRouter();
  const [showUnsavedDialog, setShowUnsavedDialog] = useState(false);
  const [pendingUrl, setPendingUrl] = useState<string | null>(null);

  const isDirty = useMemo(() => {
    if (!config) return false;
    if (fieldsDirtyRef.current) return true;
    const origPersona = (config.custom_prompts || {}) as PersonaConfig;
    const origTone = String(origPersona.tone || "professional").toLowerCase();
    const curTone = String(persona.tone || "professional").toLowerCase();
    // Normalize tone comparison (same as backend)
    const norm = (t: string) => {
      const l = t.toLowerCase();
      if (l.includes("casual") || l.includes("warm")) return "casual";
      if (l.includes("formal") || l.includes("polite")) return "formal";
      if (l.includes("friendly")) return "friendly";
      if (l.includes("empathetic")) return "empathetic";
      if (l.includes("concise") || l.includes("direct")) return "professional";
      return l || "professional";
    };
    if (norm(origTone) !== norm(curTone)) return true;
    if ((origPersona.agent_name || "") !== (persona.agent_name || "")) return true;
    if ((origPersona.greeting_message || "") !== (persona.greeting_message || "")) return true;
    if ((origPersona.industry_context || "") !== (persona.industry_context || "")) return true;
    if ((origPersona.custom_instructions || "") !== (persona.custom_instructions || "")) return true;
    if ((origPersona.business_name || "") !== (persona.business_name || "")) return true;
    if ((origPersona.business_tagline || "") !== (persona.business_tagline || "")) return true;
    if ((config.ui_brand_color || "#3B82F6").toLowerCase() !== (brandColor || "#3B82F6").toLowerCase()) return true;
    const origBusinessName = (config as unknown as { business_name?: string | null }).business_name || "";
    if ((origBusinessName || "") !== (businessName || "")) return true;
    const origLogo = (config as unknown as { logo_url?: string | null }).logo_url || null;
    if ((origLogo || null) !== (logoUrl || null)) return true;
    if ((config.require_otp ?? true) !== requireOtp) return true;
    const origPhoneOtp = (config as unknown as { require_phone_otp?: boolean }).require_phone_otp ?? false;
    if (origPhoneOtp !== requirePhoneOtp) return true;
    const mp = (config as unknown as { meeting_policy?: { meeting_mode?: string; allow_hybrid_selection?: boolean; meeting_provider_default?: string } }).meeting_policy;
    if (mp) {
      if ((mp.meeting_mode || "OFFLINE_ONLY") !== meetingMode) return true;
      if ((mp.allow_hybrid_selection ?? true) !== allowHybridSelection) return true;
      if ((mp.meeting_provider_default || "NONE") !== meetingProviderDefault) return true;
    }
    return false;
  }, [config, fieldDefs, persona, brandColor, businessName, logoUrl, requireOtp, requirePhoneOtp, meetingMode, allowHybridSelection, meetingProviderDefault]);

  useEffect(() => {
    if (!isDirty) return;
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  useEffect(() => {
    if (!isDirty) return;
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      const anchor = target.closest("a");
      if (anchor && anchor.href && anchor.target !== "_blank" && !anchor.href.includes("#")) {
        try {
          const url = new URL(anchor.href);
          if (url.origin === window.location.origin && url.pathname !== window.location.pathname) {
            e.preventDefault();
            e.stopPropagation();
            setPendingUrl(anchor.href);
            setShowUnsavedDialog(true);
          }
        } catch {}
      }
    };
    document.addEventListener("click", handleClick, true);
    return () => document.removeEventListener("click", handleClick, true);
  }, [isDirty]);

  const handleSave = async () => {
    // Validate fields before saving
    const invalid = fieldDefs.find((f) => !f.name || !f.label);
    if (invalid) {
      setSaveError("Every field needs a key and label. Please fill in all required fields.");
      return;
    }
    const dupeNames = fieldDefs.map((f) => f.name);
    if (new Set(dupeNames).size !== dupeNames.length) {
      setSaveError("Duplicate field keys detected. Each field must have a unique key.");
      return;
    }

    setIsSaving(true);
    setSaveError(null);
    setSaveSuccess(false);
    try {
      const payload: Record<string, unknown> = {
        ui_brand_color: brandColor,
        custom_prompts: persona,
        require_otp: requireOtp,
        require_phone_otp: requirePhoneOtp,
        business_name: businessName,
        meeting_mode: meetingMode,
        allow_hybrid_selection: allowHybridSelection,
        meeting_provider_default: meetingProviderDefault,
      };
      // Only send field definitions when the user actually modified them —
      // the backend validates core booking fields (name, email, phone, date,
      // time) and would reject an unrelated save otherwise.
      if (fieldsDirtyRef.current) {
        const orderedDefs = fieldDefs.map((f, i) => ({ ...f, order: i }));
        payload.field_definitions = orderedDefs;
      }
      await api.put("/dashboard/agent-config", payload);
      fieldsDirtyRef.current = false;
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2000);
      await refetch();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Failed to save");
    } finally {
      setIsSaving(false);
    }
  };

  const addField = () => {
    fieldsDirtyRef.current = true;
    const newField = makeEmptyField(fieldDefs.length);
    setFieldDefs([...fieldDefs, newField]);
    setExpandedIdx(fieldDefs.length);
  };

  const updateField = (index: number, updated: FieldDefinition) => {
    fieldsDirtyRef.current = true;
    setFieldDefs(fieldDefs.map((f, i) => (i === index ? updated : f)));
  };

  const deleteField = (index: number) => {
    fieldsDirtyRef.current = true;
    setFieldDefs(fieldDefs.filter((_, i) => i !== index));
    if (expandedIdx === index) setExpandedIdx(null);
    else if (expandedIdx !== null && expandedIdx > index) setExpandedIdx(expandedIdx - 1);
  };

  const moveField = (from: number, to: number) => {
    if (to < 0 || to >= fieldDefs.length) return;
    fieldsDirtyRef.current = true;
    const copy = [...fieldDefs];
    const [moved] = copy.splice(from, 1);
    copy.splice(to, 0, moved);
    setFieldDefs(copy);
    if (expandedIdx === from) setExpandedIdx(to);
    else if (expandedIdx === to) setExpandedIdx(from);
  };

  const updatePersona = (key: keyof PersonaConfig, value: string) => {
    setPersona((prev) => ({ ...prev, [key]: value }));
  };

  if (loading) {
    return (
      <div className="space-y-6 animate-pulse mt-2">
        {/* Header Skeleton */}
        <div className="flex items-end justify-between mb-6">
          <div className="space-y-2">
            <div className="h-8 w-64 bg-secondary rounded" />
            <div className="h-4 w-96 bg-secondary rounded" />
          </div>
          <div className="h-9 w-32 bg-secondary rounded-md" />
        </div>

        {/* Persona Box */}
        <div className="bg-card border border-border rounded-xl p-6 space-y-5">
          <div className="flex items-center gap-3 mb-2">
            <div className="size-9 rounded-lg bg-secondary" />
            <div className="space-y-1">
              <div className="h-5 w-32 bg-secondary rounded" />
              <div className="h-3 w-64 bg-secondary rounded" />
            </div>
          </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <div className="space-y-2"><div className="h-4 w-24 bg-secondary rounded" /><div className="h-10 w-full bg-secondary rounded-md" /></div>
            <div className="space-y-2"><div className="h-4 w-24 bg-secondary rounded" /><div className="h-10 w-full bg-secondary rounded-md" /></div>
            <div className="col-span-2 space-y-2"><div className="h-4 w-32 bg-secondary rounded" /><div className="h-20 w-full bg-secondary rounded-md" /></div>
          </div>
        </div>

        {/* Fields Box */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-3">
              <div className="size-9 rounded-lg bg-secondary" />
              <div className="space-y-1">
                <div className="h-5 w-32 bg-secondary rounded" />
                <div className="h-3 w-64 bg-secondary rounded" />
              </div>
            </div>
            <div className="h-9 w-24 bg-secondary rounded-md" />
          </div>
          <div className="space-y-2">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-[46px] w-full bg-secondary rounded-lg" />
            ))}
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center gap-2 text-red-500 py-8">
        <AlertCircle className="size-4" /> {error}
      </div>
    );
  }

  return (
    <>
      <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3 mb-2">
        <div>
          <h1 className="text-[22px] sm:text-[26px] md:text-[28px] font-semibold tracking-tight leading-none">Agent Personalisation</h1>
          <p className="text-muted-foreground text-xs sm:text-sm mt-1">
            Define how your AI acts and what data it must collect.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {saveSuccess && <span className="text-green-500 text-[13px]">✓ Saved</span>}
          {saveError && <span className="text-red-500 text-[13px]">{saveError}</span>}
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="text-[13px] font-medium px-4 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center gap-2"
          >
            {isSaving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            {isSaving ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>

      {/* Agent Persona — full width */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary">
            <Bot className="size-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold">Agent Persona</h3>
            <p className="text-[13px] text-muted-foreground">
              Customize how your booking agent introduces itself and interacts with customers.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-5">
          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">Agent Name</label>
            <input
              type="text"
              value={persona.agent_name || ""}
              onChange={(e) => updatePersona("agent_name", e.target.value)}
              placeholder="e.g. Booking Assistant, Dr. Smith's Office"
              className="w-full bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            />
          </div>

          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">Tone</label>
            <select
              value={persona.tone || "professional"}
              onChange={(e) => updatePersona("tone", e.target.value)}
              className="w-full bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            >
              {TONE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          <div className="col-span-2">
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">
              Greeting Message
            </label>
            <textarea
              value={persona.greeting_message || ""}
              onChange={(e) => updatePersona("greeting_message", e.target.value)}
              placeholder="e.g. Hi there! I'm here to help you book an appointment. How can I assist you today?"
              rows={2}
              className="w-full bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all resize-none"
            />
          </div>

          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">
              Industry Context
            </label>
            <input
              type="text"
              value={persona.industry_context || ""}
              onChange={(e) => updatePersona("industry_context", e.target.value)}
              placeholder="e.g. dental clinic, hair salon, consulting firm"
              className="w-full bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            />
          </div>

          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block flex items-center gap-1.5">
              <Sparkles className="size-3.5" /> Custom Instructions
            </label>
            <input
              type="text"
              value={persona.custom_instructions || ""}
              onChange={(e) => updatePersona("custom_instructions", e.target.value)}
              placeholder="e.g. Always ask about insurance provider first"
              className="w-full bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            />
          </div>

          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">
              Business Name
            </label>
            <input
              type="text"
              value={persona.business_name || ""}
              onChange={(e) => updatePersona("business_name", e.target.value)}
              placeholder="e.g. BrightSmile Dental"
              className="w-full bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            />
            <p className="text-[11px] text-muted-foreground mt-1.5">
              Shown in the chat widget header.
            </p>
          </div>

          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">
              Business Tagline
            </label>
            <input
              type="text"
              value={persona.business_tagline || ""}
              onChange={(e) => updatePersona("business_tagline", e.target.value)}
              placeholder="e.g. Dental Care · Appointment Booking"
              className="w-full bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
            />
            <p className="text-[11px] text-muted-foreground mt-1.5">
              Short line describing your profession, shown under the business name.
            </p>
          </div>

          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block flex items-center gap-1.5">
              <ImageIcon className="size-3.5" /> Chatbot Logo
            </label>
            <div className="flex items-center gap-3">
              <div
                className="size-16 rounded-xl border-2 bg-secondary flex items-center justify-center overflow-hidden shrink-0"
                style={{ borderColor: brandColor }}
              >
                {logoUrl ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={logoUrl} alt="Chatbot logo" className="size-full object-cover" />
                ) : (
                  <ImageIcon className="size-6 text-muted-foreground" />
                )}
              </div>
              <div className="flex flex-col gap-1.5">
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={logoUploading}
                    className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-md bg-primary text-primary-foreground hover:opacity-90 disabled:opacity-50"
                  >
                    {logoUploading ? <Loader2 className="size-3 animate-spin" /> : <Upload className="size-3" />}
                    {logoUrl ? "Change" : "Upload Image"}
                  </button>
                  {logoUrl && (
                    <button
                      type="button"
                      onClick={handleLogoRemove}
                      disabled={logoUploading}
                      className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-md bg-secondary border border-border hover:bg-accent disabled:opacity-50"
                    >
                      <X className="size-3" /> Remove
                    </button>
                  )}
                </div>
                <p className="text-[11px] text-muted-foreground">PNG, JPG, WEBP, SVG • Max 2MB</p>
              </div>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/svg+xml"
              className="hidden"
              onChange={handleLogoFile}
            />
            <div className="mt-3">
              <label className="text-[11px] text-muted-foreground font-medium flex items-center gap-1">
                <LinkIcon className="size-3" /> Or paste image URL
              </label>
              <div className="flex gap-2 mt-1">
                <input
                  type="url"
                  value={logoUrlInput}
                  onChange={(e) => setLogoUrlInput(e.target.value)}
                  placeholder="https://example.com/logo.png"
                  className="flex-1 bg-background border border-border px-3 py-1.5 rounded-md text-xs focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary"
                />
                <button
                  type="button"
                  onClick={handleLogoUrlSave}
                  disabled={logoUploading}
                  className="text-xs font-medium px-3 py-1.5 rounded-md bg-secondary border border-border hover:bg-accent disabled:opacity-50"
                >
                  Save
                </button>
              </div>
            </div>
            {logoError && <p className="text-xs text-red-500 mt-1.5">{logoError}</p>}
            <p className="text-[11px] text-muted-foreground mt-1.5">Shows in chatbot header, left of brand color.</p>
          </div>
          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">
              Brand Color
            </label>
            <div className="flex items-center gap-3">
              <input
                type="color"
                value={brandColor}
                onChange={(e) => setBrandColor(e.target.value)}
                className="size-10 rounded-md border border-border cursor-pointer"
              />
              <input
                type="text"
                value={brandColor}
                onChange={(e) => setBrandColor(e.target.value)}
                className="w-28 bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-mono focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
              />
            </div>
            <p className="text-[11px] text-muted-foreground mt-1.5">
              Widget theme color.
            </p>
          </div>
        </div>
      </div>

      {/* Field Definitions — full width */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-5">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary">
              <Sparkles className="size-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold">Booking Fields</h3>
              <p className="text-[13px] text-muted-foreground">
                Define what information your agent collects. Drag to reorder, click to edit.
              </p>
            </div>
          </div>
          <button
            onClick={addField}
            className="text-[13px] font-medium px-3 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity flex items-center gap-1.5"
          >
            <Plus className="size-4" /> Add Field
          </button>
        </div>

        <div className="space-y-2">
          {fieldDefs.length === 0 ? (
            <div className="text-center py-8 border border-dashed border-border rounded-lg">
              <p className="text-muted-foreground text-sm">No fields configured.</p>
              <p className="text-muted-foreground/60 text-xs mt-1">
                Click &quot;Add Field&quot; to start defining what your agent should collect.
              </p>
            </div>
          ) : (
            fieldDefs.map((field, i) => (
              <FieldRow
                key={`${field.name}-${i}`}
                field={field}
                index={i}
                expanded={expandedIdx === i}
                onToggle={() => setExpandedIdx(expandedIdx === i ? null : i)}
                onChange={(updated) => updateField(i, updated)}
                onDelete={() => deleteField(i)}
                onMoveUp={() => moveField(i, i - 1)}
                onMoveDown={() => moveField(i, i + 1)}
                isFirst={i === 0}
                isLast={i === fieldDefs.length - 1}
              />
            ))
          )}
        </div>
      </div>

      {/* Organization Settings */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary">
            <CalendarDays className="size-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold">Booking Settings</h3>
            <p className="text-[13px] text-muted-foreground">
              Control verification requirements and booking constraints.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-5">
          {/* Email OTP Verification toggle */}
          <div className="flex items-start gap-4 p-4 bg-background border border-border rounded-lg">
            <div className="flex items-center justify-center size-9 rounded-lg bg-green-500/10 text-green-500 shrink-0 mt-0.5">
              <ShieldCheck className="size-5" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
                <div>
                  <p className="text-[13px] font-semibold">Email OTP Verification</p>
                  <p className="text-[12px] text-muted-foreground mt-0.5">
                    Require email OTP verification before confirming a booking. Recommended for preventing spam.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={requireOtp}
                  onClick={() => setRequireOtp(!requireOtp)}
                  className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
                    requireOtp ? "bg-primary" : "bg-secondary"
                  }`}
                >
                  <span
                    className={`pointer-events-none inline-block size-5 rounded-full bg-white shadow-lg ring-0 transition-transform ${
                      requireOtp ? "translate-x-5" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
              <p className="text-[11px] mt-2 font-medium">
                {requireOtp ? (
                  <span className="text-green-500">✓ Enabled — customers must verify via email OTP</span>
                ) : (
                  <span className="text-amber-500">⚠ Disabled — bookings will be confirmed without email OTP</span>
                )}
              </p>
            </div>
          </div>
          {/* Phone OTP Verification toggle */}
          <div className="flex items-start gap-4 p-4 bg-background border border-border rounded-lg">
            <div className="flex items-center justify-center size-9 rounded-lg bg-blue-500/10 text-blue-500 shrink-0 mt-0.5">
              <ShieldCheck className="size-5" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
                <div>
                  <p className="text-[13px] font-semibold">Phone OTP Verification</p>
                  <p className="text-[12px] text-muted-foreground mt-0.5">
                    Require SMS OTP to the customer&apos;s phone before confirming.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={requirePhoneOtp}
                  onClick={() => setRequirePhoneOtp(!requirePhoneOtp)}
                  className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background ${
                    requirePhoneOtp ? "bg-primary" : "bg-secondary"
                  }`}
                >
                  <span
                    className={`pointer-events-none inline-block size-5 rounded-full bg-white shadow-lg ring-0 transition-transform ${
                      requirePhoneOtp ? "translate-x-5" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
              <p className="text-[11px] mt-2 font-medium">
                {requirePhoneOtp ? (
                  <span className="text-blue-500">✓ Enabled — customers must verify via SMS OTP</span>
                ) : (
                  <span className="text-amber-500">⚠ Disabled — bookings will be confirmed without phone OTP</span>
                )}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Meeting Policy - moved from Integrations > Meeting Providers */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center justify-center size-9 rounded-lg bg-violet-500/10 text-violet-500">
            <CalendarDays className="size-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold">Meeting Policy</h3>
            <p className="text-[13px] text-muted-foreground">Control which meeting types are allowed in booking flows.</p>
          </div>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
          <div>
            <label className="text-[13px] font-semibold text-foreground mb-1.5 block">Meeting Mode</label>
            <select
              value={meetingMode}
              onChange={(e) => {
                const next = e.target.value as "OFFLINE_ONLY" | "ONLINE_ONLY" | "HYBRID";
                setMeetingMode(next);
                if (next === "OFFLINE_ONLY") setMeetingProviderDefault("NONE");
              }}
              className="w-full bg-background border border-border rounded-lg px-4 py-2.5 text-sm"
            >
              <option value="OFFLINE_ONLY">Offline only</option>
              <option value="ONLINE_ONLY">Online only</option>
              <option value="HYBRID">Hybrid</option>
            </select>
          </div>
          <div>
            <label className="text-[13px] font-semibold text-foreground mb-1.5 block">
              Default Online Provider {meetingMode === "OFFLINE_ONLY" && <span className="text-[11px] font-normal text-muted-foreground">(disabled for Offline)</span>}
            </label>
            <select
              value={meetingMode === "OFFLINE_ONLY" ? "NONE" : meetingProviderDefault}
              onChange={(e) => setMeetingProviderDefault(e.target.value as "NONE" | "GOOGLE_MEET")}
              disabled={meetingMode === "OFFLINE_ONLY"}
              className={`w-full bg-background border border-border rounded-lg px-4 py-2.5 text-sm ${meetingMode === "OFFLINE_ONLY" ? "opacity-50 cursor-not-allowed bg-muted" : ""}`}
              title={meetingMode === "OFFLINE_ONLY" ? "Enable Online or Hybrid mode to select a provider" : undefined}
            >
              <option value="NONE">None</option>
              <option value="GOOGLE_MEET">Google Meet</option>
            </select>
            {meetingMode === "OFFLINE_ONLY" && (
              <p className="text-[11px] text-muted-foreground mt-1">Provider selection is disabled for Offline-only bookings.</p>
            )}
          </div>
        </div>
        <div className="flex items-center justify-between border border-border rounded-lg px-4 py-3">
          <div>
            <p className="text-sm font-medium">Allow hybrid user selection</p>
            <p className="text-xs text-muted-foreground">When disabled in HYBRID mode, booking flow is restricted to a single deterministic option.</p>
          </div>
          <button
            onClick={() => setAllowHybridSelection((v) => !v)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${allowHybridSelection ? "bg-green-500" : "bg-muted"}`}
          >
            <span className={`inline-block h-4 w-4 rounded-full bg-white transition-transform ${allowHybridSelection ? "translate-x-6" : "translate-x-1"}`} />
          </button>
        </div>
      </div>

      {/* API Key - hidden: not useful for users, use Widget Token in Integration → Web Widget instead */}
      <div className="hidden">
        <div className="flex items-center gap-3 mb-5">
          <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary">
            <KeyRound className="size-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold">API Key</h3>
            <p className="text-[13px] text-muted-foreground">
              Use this key to authenticate legacy integrations with your booking agent.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex-1 flex items-center gap-2 bg-background border border-border rounded-md px-3.5 py-2.5 min-w-0">
            <code className="text-[13px] font-mono text-foreground truncate">
              {apiKeyRevealed && apiKeyFull
                ? apiKeyFull
                : (config?.api_key || (apiKeyFull ? apiKeyFull.slice(0, 6) + "..." + apiKeyFull.slice(-4) : ""))}
            </code>
          </div>
          <button
            type="button"
            onClick={toggleApiKeyVisibility}
            disabled={apiKeyLoading || apiKeyRegenerating}
            title={apiKeyRevealed ? "Hide API key" : "Reveal API key"}
            className="inline-flex items-center justify-center size-9 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50"
          >
            {apiKeyLoading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : apiKeyRevealed ? (
              <EyeOff className="size-4" />
            ) : (
              <Eye className="size-4" />
            )}
          </button>
          <button
            type="button"
            onClick={copyApiKey}
            disabled={apiKeyRegenerating}
            title="Copy API key"
            className="inline-flex items-center justify-center size-9 rounded-md border border-border hover:bg-accent transition-colors disabled:opacity-50"
          >
            {apiKeyCopied ? (
              <Check className="size-4 text-green-500" />
            ) : (
              <Copy className="size-4" />
            )}
          </button>
          <button
            type="button"
            onClick={regenerateApiKey}
            disabled={apiKeyRegenerating || apiKeyLoading}
            title="Regenerate API key"
            className="inline-flex items-center justify-center size-9 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-accent transition-colors disabled:opacity-50"
          >
            {apiKeyRegenerating ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />}
          </button>
        </div>
        {apiKeyError && (
          <p className="text-[11px] text-red-500 mt-1">{apiKeyError}</p>
        )}
      </div>

      {/* Knowledge Base (Vectorless RAG: PDF upload + website crawl) */}
      <KnowledgeBaseSection />

      <AlertDialog open={showUnsavedDialog} onOpenChange={setShowUnsavedDialog}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Unsaved changes</AlertDialogTitle>
            <AlertDialogDescription>
              You have unsaved changes. If you leave now, your changes will be lost. Save before leaving?
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-col sm:flex-row gap-2">
            <AlertDialogCancel onClick={() => { setShowUnsavedDialog(false); setPendingUrl(null); }}>Stay on page</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                fieldsDirtyRef.current = false;
                setShowUnsavedDialog(false);
                if (pendingUrl) window.location.href = pendingUrl;
                setPendingUrl(null);
              }}
              className="bg-amber-500 hover:bg-amber-600"
            >
              Discard changes
            </AlertDialogAction>
            <AlertDialogAction
              onClick={async () => {
                setShowUnsavedDialog(false);
                await handleSave();
                if (pendingUrl) window.location.href = pendingUrl;
                setPendingUrl(null);
              }}
            >
              Save and leave
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
