"use client";

import { useEffect, useState } from "react";
import {
  X, Loader2, AlertCircle, UploadCloud, Globe,
  Building2, ClipboardCheck, ChevronLeft, Plus, Tag, Pencil,
} from "lucide-react";
import {
  useBusinessProfile,
  saveBusinessProfile,
  uploadPdfDocument,
  startWebsiteCrawl,
  MAX_DOCS_PER_ORG,
  type BusinessProfile,
  type SourceContextInput,
  type CrawlContextInput,
} from "@/hooks/use-dashboard";

// â”€â”€â”€ Constants shared with the server contract â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"];
const ACCEPT_TYPES = SUPPORTED_EXTENSIONS.join(",");
const MAX_FILE_BYTES = 25 * 1024 * 1024;

const CATEGORY_OPTIONS = [
  "Pricing",
  "FAQ",
  "Services",
  "Policy",
  "Brochure",
  "Menu",
  "Other",
];

type WizardMode = "upload" | "crawl";

interface Props {
  mode: WizardMode;
  docCount?: number;
  onClose: () => void;
  onUploadDone: (filename: string, pages: number) => void;
  onCrawlStarted: (crawlId: string, url: string) => void;
}

// â”€â”€â”€ Tiny tag editor â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function TagEditor({
  tags,
  onChange,
  placeholder,
}: {
  tags: string[];
  onChange: (t: string[]) => void;
  placeholder: string;
}) {
  const [draft, setDraft] = useState("");

  const commit = () => {
    const v = draft.trim();
    if (!v) return;
    if (!tags.some((t) => t.toLowerCase() === v.toLowerCase()) && tags.length < 20) {
      onChange([...tags, v]);
    }
    setDraft("");
  };

  return (
    <div>
      <div className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              commit();
            }
          }}
          onBlur={commit}
          placeholder={placeholder}
          className="flex-1 bg-background border border-border text-foreground px-3 py-2 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
        />
        <button
          type="button"
          onClick={commit}
          className="px-3 rounded-md border border-border bg-background hover:bg-secondary transition-colors flex items-center"
          aria-label="Add tag"
        >
          <Plus className="size-4 text-muted-foreground" />
        </button>
      </div>
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {tags.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-primary/10 text-primary text-[11px] font-medium"
            >
              <Tag className="size-3" />
              {t}
              <button
                type="button"
                onClick={() => onChange(tags.filter((x) => x !== t))}
                className="hover:text-red-500"
                aria-label={`Remove ${t}`}
              >
                <X className="size-3" />
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// â”€â”€â”€ Field primitives â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function FieldLabel({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <label className="block text-[12px] font-medium text-foreground mb-1.5">
      {children}
      {required && <span className="text-red-500"> *</span>}
    </label>
  );
}

const inputCls =
  "w-full bg-background border border-border text-foreground px-3 py-2 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all placeholder:text-muted-foreground/60";

// â”€â”€â”€ Main wizard â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export function AddKnowledgeModal({
  mode,
  docCount = 0,
  onClose,
  onUploadDone,
  onCrawlStarted,
}: Props) {
  // Wizard steps: source is always first; the business step is only shown
  // when no profile has been saved yet (or the admin explicitly chooses to
  // edit it from the review screen) â€” repeat uploads skip straight through.
  const [stepKey, setStepKey] = useState<"source" | "business" | "confirm">("source");
  const [visitBusiness, setVisitBusiness] = useState(false);

  const { data: savedProfile, loading: profileLoading } = useBusinessProfile();

  // Step 1 state â€” business profile
  const [profile, setProfile] = useState<BusinessProfile>({
    business_name: "",
    business_description: "",
    products_services: "",
    target_customers: "",
    key_policies: "",
    answer_language_tone: "",
  });
  const [profileDirty, setProfileDirty] = useState(false);
  const [profileLoadedOnce, setProfileLoadedOnce] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);

  // Step 2 state â€” source
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [sourceDescription, setSourceDescription] = useState("");
  const [category, setCategory] = useState("");
  const [questions, setQuestions] = useState<string[]>([]);
  const [pagesThatMatter, setPagesThatMatter] = useState("");
  const [notCovered, setNotCovered] = useState("");

  // Submit state
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  // Prefill once the saved profile arrives.
  useEffect(() => {
    if (savedProfile && !profileLoadedOnce) {
      setProfile({
        business_name: savedProfile.business_name ?? "",
        business_description: savedProfile.business_description ?? "",
        products_services: savedProfile.products_services ?? "",
        target_customers: savedProfile.target_customers ?? "",
        key_policies: savedProfile.key_policies ?? "",
        answer_language_tone: savedProfile.answer_language_tone ?? "",
      });
      setProfileLoadedOnce(true);
    }
  }, [savedProfile, profileLoadedOnce]);

  const atLimit = docCount >= MAX_DOCS_PER_ORG;

  // The business step is required only the first time (until a profile is
  // saved) or when the admin explicitly asks to edit it from the review.
  const needsBusiness =
    !profileLoadedOnce || !savedProfile?.configured || visitBusiness;

  const dirty =
    profileDirty || !!file || !!url.trim() || !!sourceDescription.trim() ||
    !!category || questions.length > 0 || !!pagesThatMatter.trim() || !!notCovered.trim();

  const attemptClose = () => {
    if (dirty && !window.confirm("Discard this draft? The information you entered will be lost.")) {
      return;
    }
    onClose();
  };

  const patchProfile = (patch: Partial<BusinessProfile>) => {
    setProfile((p) => ({ ...p, ...patch }));
    setProfileDirty(true);
  };

  // Source â†’ business (first time) or straight to review (profile exists).
  const continueFromSource = () => {
    setError(null);
    if (mode === "upload") {
      if (!file) {
        setError("Please select a document to upload.");
        return;
      }
    } else if (!url.trim()) {
      setError("Please enter your website URL.");
      return;
    }
    if (!sourceDescription.trim()) {
      setError(
        mode === "upload"
          ? "Please describe what this document is about."
          : "Please describe what this website contains."
      );
      return;
    }
    setStepKey(needsBusiness ? "business" : "confirm");
  };

  // Business â†’ confirm: persist profile (only when touched/unconfigured).
  const continueFromBusiness = async () => {
    setError(null);
    if (!profile.business_description.trim()) {
      setError("Please describe what your business does.");
      return;
    }
    if (profileDirty || !savedProfile?.configured) {
      setSavingProfile(true);
      try {
        await saveBusinessProfile({
          ...profile,
          business_name: profile.business_name?.trim() || null,
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not save business profile");
        setSavingProfile(false);
        return;
      }
      setSavingProfile(false);
    }
    setStepKey("confirm");
  };

  const pickFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    setError(null);
    const ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
    if (!SUPPORTED_EXTENSIONS.includes(ext)) {
      setError("Unsupported file type. Supported: PDF, DOCX, XLSX, CSV, TXT, MD");
      return;
    }
    if (f.size > MAX_FILE_BYTES) {
      setError("File exceeds the 25MB limit.");
      return;
    }
    setFile(f);
  };

  const submit = async () => {
    setError(null);
    setSubmitting(true);
    try {
      if (mode === "upload") {
        if (!file) throw new Error("No file selected");
        const ctx: SourceContextInput = {
          source_description: sourceDescription.trim(),
          ...(category ? { source_category: category } : {}),
          ...(questions.length ? { questions_to_answer: questions } : {}),
        };
        const res = await uploadPdfDocument(file, ctx);
        onUploadDone(res.filename, res.pages);
      } else {
        const ctx: CrawlContextInput = {
          source_description: sourceDescription.trim(),
          ...(category ? { source_category: category } : {}),
          ...(questions.length ? { questions_to_answer: questions } : {}),
          ...(pagesThatMatter.trim() ? { pages_that_matter: pagesThatMatter.trim() } : {}),
          ...(notCovered.trim() ? { not_covered: notCovered.trim() } : {}),
        };
        let normalized = url.trim();
        if (!/^https?:\/\//i.test(normalized)) normalized = `https://${normalized}`;
        const res = await startWebsiteCrawl(normalized, ctx);
        onCrawlStarted(res.crawl_id, normalized);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
      setSubmitting(false);
      return;
    }
    setSubmitting(false);
  };

  const steps = [
    {
      key: "source" as const,
      icon: mode === "upload" ? UploadCloud : Globe,
      label: mode === "upload" ? "Document" : "Website",
    },
    ...(needsBusiness
      ? [{ key: "business" as const, icon: Building2, label: "Your business" }]
      : []),
    { key: "confirm" as const, icon: ClipboardCheck, label: "Confirm" },
  ];
  const stepIndex = Math.max(
    0,
    steps.findIndex((s) => s.key === stepKey)
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 sm:p-6 animate-in fade-in duration-200">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-background/80 backdrop-blur-sm" onClick={attemptClose} />

      {/* Modal */}
      <div className="relative w-full max-w-lg bg-card border border-border rounded-2xl shadow-2xl overflow-hidden animate-in zoom-in-95 duration-300 max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="p-4 sm:p-5 border-b border-border flex items-start justify-between gap-3 shrink-0">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary shrink-0">
              {mode === "upload" ? <UploadCloud className="size-5" /> : <Globe className="size-5" />}
            </div>
            <div>
              <h3 className="text-base font-semibold leading-tight">
                {mode === "upload" ? "Add Document Knowledge" : "Add Website Knowledge"}
              </h3>
              <p className="text-[12px] text-muted-foreground">
                Step {stepIndex + 1} of {steps.length} Â· {steps[stepIndex]?.label}
              </p>
            </div>
          </div>
          <button
            onClick={attemptClose}
            disabled={submitting}
            className="p-1.5 rounded-md hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors disabled:opacity-50"
            aria-label="Close"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* Stepper */}
        <div className="flex items-center gap-1 px-4 sm:px-5 pt-3 shrink-0">
          {steps.map((s, i) => (
            <div key={s.key} className="flex items-center flex-1 last:flex-none">
              <div
                className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-[11px] font-medium whitespace-nowrap ${
                  s.key === stepKey
                    ? "bg-primary/10 text-primary"
                    : i < stepIndex
                    ? "text-green-500"
                    : "text-muted-foreground/60"
                }`}
              >
                <s.icon className="size-3.5" />
                <span className="hidden sm:inline">{s.label}</span>
              </div>
              {i < steps.length - 1 && (
                <div className={`h-px flex-1 mx-1 ${i < stepIndex ? "bg-green-500/40" : "bg-border"}`} />
              )}
            </div>
          ))}
        </div>

        {/* Body */}
        <div className="p-4 sm:p-5 space-y-4 overflow-y-auto custom-scrollbar">
          {error && (
            <div className="flex items-start gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-500 text-[12px]">
              <AlertCircle className="size-4 shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {/* STEP 1 â€” source details */}
          {stepKey === "source" && (
            <>
              {mode === "upload" ? (
                <>
                  <div>
                    <FieldLabel required>Select document</FieldLabel>
                    <label
                      className={`flex flex-col items-center justify-center gap-2 border border-dashed rounded-xl p-6 cursor-pointer transition-colors ${
                        file
                          ? "border-green-500/50 bg-green-500/5"
                          : "border-border hover:border-primary/50 hover:bg-secondary/30"
                      }`}
                    >
                      <input
                        type="file"
                        accept={ACCEPT_TYPES}
                        className="hidden"
                        onChange={pickFile}
                      />
                      <UploadCloud className={`size-6 ${file ? "text-green-500" : "text-muted-foreground"}`} />
                      {file ? (
                        <span className="text-[13px] font-medium text-foreground truncate max-w-full px-4">
                          {file.name}
                          <span className="block text-[11px] text-muted-foreground font-normal text-center">
                            {(file.size / (1024 * 1024)).toFixed(1)} MB Â· click to replace
                          </span>
                        </span>
                      ) : (
                        <span className="text-[12px] text-muted-foreground text-center">
                          Click to choose a file
                          <span className="block text-[11px] opacity-70">PDF, DOCX, XLSX, CSV, TXT, MD Â· max 25MB</span>
                        </span>
                      )}
                    </label>
                    {atLimit && (
                      <p className="text-[11px] text-amber-500 mt-1.5">
                        âš  Maximum of {MAX_DOCS_PER_ORG} documents reached â€” delete one first.
                      </p>
                    )}
                  </div>
                </>
              ) : (
                <div>
                  <FieldLabel required>Website URL</FieldLabel>
                  <input
                    type="text"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    placeholder="https://your-website.com"
                    className={inputCls}
                  />
                </div>
              )}
              <div>
                <FieldLabel required>
                  {mode === "upload" ? "What is this document about?" : "What does this website contain?"}
                </FieldLabel>
                <textarea
                  value={sourceDescription}
                  onChange={(e) => setSourceDescription(e.target.value)}
                  placeholder={
                    mode === "upload"
                      ? "e.g. Our 2026 service price list with consultation fees for every treatment"
                      : "e.g. Company website with service pages, pricing and customer FAQs"
                  }
                  rows={3}
                  maxLength={2000}
                  className={`${inputCls} resize-none`}
                />
              </div>
              <div>
                <FieldLabel>Category</FieldLabel>
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  className={inputCls}
                >
                  <option value="">Select a category (optional)</option>
                  {CATEGORY_OPTIONS.map((c) => (
                    <option key={c} value={c}>{c}</option>
                  ))}
                </select>
              </div>
              <div>
                <FieldLabel>Which customer questions should this answer?</FieldLabel>
                <TagEditor
                  tags={questions}
                  onChange={setQuestions}
                  placeholder="Type a question and press Enter"
                />
              </div>
              {mode === "crawl" && (
                <>
                  <div>
                    <FieldLabel>Pages or sections that matter most</FieldLabel>
                    <input
                      type="text"
                      value={pagesThatMatter}
                      onChange={(e) => setPagesThatMatter(e.target.value)}
                      placeholder="e.g. /pricing, /services, FAQ page"
                      maxLength={2000}
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <FieldLabel>Anything this website does NOT cover</FieldLabel>
                    <textarea
                      value={notCovered}
                      onChange={(e) => setNotCovered(e.target.value)}
                      placeholder="Helps the AI avoid wrong answers, e.g. insurance claims are handled offline"
                      rows={2}
                      maxLength={2000}
                      className={`${inputCls} resize-none`}
                    />
                  </div>
                </>
              )}
            </>
          )}

          {/* STEP 2 â€” business profile */}
          {stepKey === "business" && (
            <>
              <p className="text-[12px] text-muted-foreground">
                This helps our AI answer questions about your business accurately â€” even when
                the answer isn&apos;t written in this content.
              </p>
              {profileLoading && !profileLoadedOnce ? (
                <div className="flex items-center gap-2 text-muted-foreground text-sm py-6 justify-center">
                  <Loader2 className="size-4 animate-spin" /> Loading saved profileâ€¦
                </div>
              ) : (
                <div className="space-y-3">
                  <div>
                    <FieldLabel>Business name</FieldLabel>
                    <input
                      type="text"
                      value={profile.business_name ?? ""}
                      onChange={(e) => patchProfile({ business_name: e.target.value })}
                      placeholder="e.g. Smile Dental Clinic"
                      className={inputCls}
                    />
                  </div>
                  <div>
                    <FieldLabel required>What does your business do?</FieldLabel>
                    <textarea
                      value={profile.business_description}
                      onChange={(e) => patchProfile({ business_description: e.target.value })}
                      placeholder="e.g. We are a dental clinic in Pune offering implants, braces and routine check-upsâ€¦"
                      rows={3}
                      maxLength={2000}
                      className={`${inputCls} resize-none`}
                    />
                  </div>
                  <div>
                    <FieldLabel>Products / services offered</FieldLabel>
                    <textarea
                      value={profile.products_services ?? ""}
                      onChange={(e) => patchProfile({ products_services: e.target.value })}
                      placeholder="List your main services, separated by commas"
                      rows={2}
                      maxLength={2000}
                      className={`${inputCls} resize-none`}
                    />
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <FieldLabel>Target customers</FieldLabel>
                      <input
                        type="text"
                        value={profile.target_customers ?? ""}
                        onChange={(e) => patchProfile({ target_customers: e.target.value })}
                        placeholder="e.g. Families, working professionals"
                        maxLength={1000}
                        className={inputCls}
                      />
                    </div>
                    <div>
                      <FieldLabel>Answer language / tone</FieldLabel>
                      <input
                        type="text"
                        value={profile.answer_language_tone ?? ""}
                        onChange={(e) => patchProfile({ answer_language_tone: e.target.value })}
                        placeholder="e.g. English, warm and concise"
                        maxLength={300}
                        className={inputCls}
                      />
                    </div>
                  </div>
                  <div>
                    <FieldLabel>Key policies customers should know</FieldLabel>
                    <textarea
                      value={profile.key_policies ?? ""}
                      onChange={(e) => patchProfile({ key_policies: e.target.value })}
                      placeholder="e.g. Free first consultation; cancellations accepted up to 24h before the slot"
                      rows={2}
                      maxLength={2000}
                      className={`${inputCls} resize-none`}
                    />
                  </div>
                </div>
              )}
            </>
          )}

          {/* STEP 3 â€” review & confirm */}
          {stepKey === "confirm" && (
            <>
              <p className="text-[12px] text-muted-foreground">
                Please review the details below. They will be used to teach the AI about your
                business and this content.
              </p>
              <div className="rounded-xl border border-border divide-y divide-border overflow-hidden">
                <ReviewRow label="Source" value={mode === "upload" ? file?.name ?? "â€”" : url} mono={mode === "crawl"} />
                <ReviewRow label="About this content" value={sourceDescription} />
                {category && <ReviewRow label="Category" value={category} />}
                {questions.length > 0 && (
                  <ReviewRow label="Should answer" value={questions.join(", ")} />
                )}
                {pagesThatMatter.trim() && <ReviewRow label="Key pages" value={pagesThatMatter} />}
                {notCovered.trim() && <ReviewRow label="Not covered" value={notCovered} />}
                <ReviewRow
                  label="Business"
                  value={[profile.business_name, profile.business_description]
                    .filter(Boolean)
                    .join(" — ")}
                  onEdit={() => {
                    setVisitBusiness(true);
                    setStepKey("business");
                  }}
                />
                {(profile.products_services || profile.key_policies) && (
                  <ReviewRow
                    label="Services / policies"
                    value={[profile.products_services, profile.key_policies]
                      .filter(Boolean)
                      .join(" · ")}
                    onEdit={() => {
                      setVisitBusiness(true);
                      setStepKey("business");
                    }}
                  />
                )}
              </div>
              <label className="flex items-start gap-2.5 cursor-pointer select-none pt-1">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(e) => setConfirmed(e.target.checked)}
                  className="mt-0.5 size-4 accent-[var(--primary)]"
                />
                <span className="text-[12px] text-foreground">
                  I confirm this content belongs to my business and may be used by the AI
                  assistant to answer customer questions.
                </span>
              </label>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="p-4 sm:p-5 border-t border-border flex items-center justify-between gap-3 shrink-0 bg-card">
          <button
            type="button"
            onClick={() => {
              if (stepKey === "source") attemptClose();
              else if (stepKey === "business") setStepKey("source");
              else setStepKey(needsBusiness ? "business" : "source");
            }}
            disabled={submitting || savingProfile}
            className="text-[13px] font-medium px-3 py-2 rounded-md border border-border bg-background hover:bg-secondary transition-colors flex items-center gap-1.5 disabled:opacity-50"
          >
            <ChevronLeft className="size-4" />
            {stepKey === "source" ? "Cancel" : "Back"}
          </button>
          {stepKey !== "confirm" ? (
            <button
              type="button"
              onClick={stepKey === "source" ? continueFromSource : continueFromBusiness}
              disabled={
                submitting ||
                savingProfile ||
                (stepKey === "source" && profileLoading && !profileLoadedOnce) ||
                (stepKey === "business" && !profile.business_description.trim())
              }
              className="text-[13px] font-medium px-4 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity flex items-center gap-1.5 disabled:opacity-50"
            >
              {savingProfile && <Loader2 className="size-4 animate-spin" />}
              Continue
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              disabled={submitting || !confirmed}
              className="text-[13px] font-medium px-4 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity flex items-center gap-1.5 disabled:opacity-50"
            >
              {submitting && <Loader2 className="size-4 animate-spin" />}
              {mode === "upload" ? "Upload & Train Agent" : "Start Crawl"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function ReviewRow({
  label,
  value,
  mono,
  onEdit,
}: {
  label: string;
  value: string;
  mono?: boolean;
  onEdit?: () => void;
}) {
  return (
    <div className="px-3 py-2.5 grid grid-cols-[110px_1fr_auto] gap-3 items-start bg-background/40">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium pt-0.5">
        {label}
      </span>
      <span
        className={`text-[12px] text-foreground break-words ${mono ? "font-mono" : ""}`}
      >
        {value || "—"}
      </span>
      {onEdit ? (
        <button
          type="button"
          onClick={onEdit}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline shrink-0 pt-0.5"
        >
          <Pencil className="size-3" /> Edit
        </button>
      ) : null}
    </div>
  );
}
