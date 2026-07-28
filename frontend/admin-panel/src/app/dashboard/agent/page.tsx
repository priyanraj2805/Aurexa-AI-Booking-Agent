"use client";

import { useState, useEffect } from "react";
import {
  Loader2, AlertCircle, Save, Bot, Sparkles,
  Plus, GripVertical, Trash2, ChevronDown, ChevronUp,
  ShieldCheck, CalendarDays,
} from "lucide-react";
import { useAgentConfig, type PersonaConfig, type FieldDefinition, type FieldType } from "@/hooks/use-dashboard";
import { api } from "@/lib/api";
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

  // Persona state
  const [persona, setPersona] = useState<PersonaConfig>({
    agent_name: "",
    greeting_message: "",
    tone: "professional",
    industry_context: "",
    custom_instructions: "",
  });

  // Rich field definitions state
  const [fieldDefs, setFieldDefs] = useState<FieldDefinition[]>([]);
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  // Booking settings state
  const [requireOtp, setRequireOtp] = useState(true);

  // Sync config → local state when loaded
  useEffect(() => {
    if (config) {
      setBrandColor(config.ui_brand_color || "#3B82F6");
      setRequireOtp(config.require_otp ?? true);
      if (config.custom_prompts && typeof config.custom_prompts === "object") {
        setPersona({
          agent_name: config.custom_prompts.agent_name || "",
          greeting_message: config.custom_prompts.greeting_message || "",
          tone: config.custom_prompts.tone || "professional",
          industry_context: config.custom_prompts.industry_context || "",
          custom_instructions: config.custom_prompts.custom_instructions || "",
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
      // Reindex order based on array position
      const orderedDefs = fieldDefs.map((f, i) => ({ ...f, order: i }));
      await api.put("/dashboard/agent-config", {
        field_definitions: orderedDefs,
        ui_brand_color: brandColor,
        custom_prompts: persona,
        require_otp: requireOtp,
      });
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
    const newField = makeEmptyField(fieldDefs.length);
    setFieldDefs([...fieldDefs, newField]);
    setExpandedIdx(fieldDefs.length);
  };

  const updateField = (index: number, updated: FieldDefinition) => {
    setFieldDefs(fieldDefs.map((f, i) => (i === index ? updated : f)));
  };

  const deleteField = (index: number) => {
    setFieldDefs(fieldDefs.filter((_, i) => i !== index));
    if (expandedIdx === index) setExpandedIdx(null);
    else if (expandedIdx !== null && expandedIdx > index) setExpandedIdx(expandedIdx - 1);
  };

  const moveField = (from: number, to: number) => {
    if (to < 0 || to >= fieldDefs.length) return;
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
          {/* OTP Verification toggle */}
          <div className="flex items-start gap-4 p-4 bg-background border border-border rounded-lg">
            <div className="flex items-center justify-center size-9 rounded-lg bg-green-500/10 text-green-500 shrink-0 mt-0.5">
              <ShieldCheck className="size-5" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
                <div>
                  <p className="text-[13px] font-semibold">OTP Verification</p>
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
                  <span className="text-amber-500">⚠ Disabled — bookings will be confirmed without OTP</span>
                )}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Organization Info */}
      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        <h3 className="text-base font-semibold mb-5">Organization Info</h3>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">Org Name</label>
            <input
              type="text"
              value={config?.org_name || ""}
              disabled
              className="w-full bg-background border border-border text-muted-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans cursor-not-allowed"
            />
          </div>

          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">API Key</label>
            <input
              type="text"
              value={config?.api_key || ""}
              disabled
              className="w-full bg-background border border-border text-muted-foreground px-3.5 py-2.5 rounded-md text-[13px] font-mono cursor-not-allowed"
            />
          </div>

          <div>
            <label className="text-[13px] text-muted-foreground font-medium mb-2 block">Brand Color</label>
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
          </div>
        </div>
      </div>

      {/* Knowledge Base (Vectorless RAG: PDF upload + website crawl) */}
      <KnowledgeBaseSection />
    </>
  );
}
