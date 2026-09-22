"use client";

import { useState, useEffect, useRef } from "react";
import {
  Loader2, AlertCircle, FileText, Trash2, Upload,
  Globe, RefreshCw, X, FileUp, ChevronDown,
} from "lucide-react";
import {
  useDocuments, useCrawlStatus, useAgentConfig, useWebsiteSummary,
  deletePdfDocument, deleteWebsiteData,
  MAX_DOCS_PER_ORG,
  type DocumentListItem,
} from "@/hooks/use-dashboard";
import { AddKnowledgeModal } from "./add-knowledge-modal";

// ─── Small toast helper (inline) ────────────────────────────────────────

interface Toast {
  id: number;
  message: string;
  kind: "success" | "error";
}

function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = (message: string, kind: Toast["kind"] = "success") => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
  };
  return { toasts, push };
}

// ─── Document list item ─────────────────────────────────────────────────

function DocumentCard({
  doc,
  deleting,
  onDelete,
}: {
  doc: DocumentListItem;
  deleting: boolean;
  onDelete: () => void;
}) {
  const uploaded = doc.uploaded_at
    ? new Date(doc.uploaded_at).toLocaleString()
    : null;
  return (
    <div className="flex items-center gap-3 px-3 py-2.5 bg-background border border-border rounded-lg group">
      <div className="flex items-center justify-center size-8 rounded-md bg-red-500/10 text-red-500 shrink-0">
        <FileText className="size-4" />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[13px] font-medium text-foreground truncate">
          {doc.filename}
        </p>
        <p className="text-[11px] text-muted-foreground">
          {doc.pages} {doc.pages === 1 ? "page" : "pages"}
          {uploaded ? ` • ${uploaded}` : ""}
        </p>
      </div>
      <button
        onClick={onDelete}
        disabled={deleting}
        className="size-8 inline-flex items-center justify-center rounded-md text-muted-foreground hover:text-red-500 hover:bg-red-500/10 transition-colors disabled:opacity-50"
        aria-label={`Delete ${doc.filename}`}
        title="Delete document"
      >
        {deleting ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Trash2 className="size-4" />
        )}
      </button>
    </div>
  );
}

// ─── Knowledge Base section ────────────────────────────────────────────

// Poll every 30s: Firecrawl's default plan rate limit is 6 req/min, so a
// faster cadence exhausts the quota (each poll is one API call).
const CRAWL_POLL_MS = 30000;

export function KnowledgeBaseSection() {
  const { toasts, push } = useToasts();
  const { data: docs, loading: docsLoading, error: docsError, refetch: refetchDocs } = useDocuments();
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  // Dropdown state
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Modal state
  const [modalMode, setModalMode] = useState<"upload" | "crawl" | null>(null);

  // Crawl state
  const [crawlId, setCrawlId] = useState<string | null>(null);
  const { data: crawlStatus, refetch: refetchCrawl } = useCrawlStatus(crawlId);
  const { data: agentConfig } = useAgentConfig();
  // DB truth: what is actually stored (survives refreshes; never auto-deleted).
  const { data: websiteSummary, refetch: refetchWebsite } = useWebsiteSummary();
  const [pendingWebsiteDelete, setPendingWebsiteDelete] = useState(false);

  const docList = docs ?? [];
  const atLimit = docList.length >= MAX_DOCS_PER_ORG;

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    if (dropdownOpen) document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [dropdownOpen]);

  // Restore in-progress crawl state after page refresh.
  useEffect(() => {
    if (!agentConfig) return;
    const terminal = ["completed", "failed", "cancelled"];
    const storedStatus = (agentConfig.crawl_status || "").toLowerCase();
    if (agentConfig.crawl_id && !crawlId && !terminal.includes(storedStatus)) {
      setCrawlId(agentConfig.crawl_id);
    }
  }, [agentConfig]);

  // Poll while crawl is active
  useEffect(() => {
    if (!crawlId) return;
    const isDone = (crawlStatus?.status || "").toLowerCase();
    if (isDone === "completed" || isDone === "failed" || isDone === "cancelled") return;
    const t = setInterval(() => { refetchCrawl(); }, CRAWL_POLL_MS);
    return () => clearInterval(t);
  }, [crawlId, crawlStatus, refetchCrawl]);

  const onDelete = async (doc: DocumentListItem) => {
    if (!confirm(`Delete "${doc.filename}"? This removes it from the agent's knowledge base.`)) return;
    setPendingDelete(doc.id);
    try {
      await deletePdfDocument(doc.id);
      push(`Deleted "${doc.filename}".`);
      await refetchDocs();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Delete failed";
      push(msg, "error");
    } finally {
      setPendingDelete(null);
    }
  };

  const onDeleteWebsite = async () => {
    if (!confirm("Delete all crawled website data? The agent will no longer answer from these pages.")) return;
    setPendingWebsiteDelete(true);
    try {
      const res = await deleteWebsiteData();
      push(`Deleted crawled website data (${res.deleted_chunks} chunks).`);
      setCrawlId(null);
      await refetchWebsite();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Delete failed";
      push(msg, "error");
    } finally {
      setPendingWebsiteDelete(false);
    }
  };

  const crawlStatusText = crawlStatus?.status || (crawlId ? "starting" : "idle");
  const crawlStatusColor =
    crawlStatusText.toLowerCase() === "completed"
      ? "text-green-500 bg-green-500/10"
      : crawlStatusText.toLowerCase() === "failed"
      ? "text-red-500 bg-red-500/10"
      : "text-blue-500 bg-blue-500/10";

  return (
    <>
      {/* Toasts */}
      <div className="fixed bottom-4 right-4 z-50 space-y-2 max-w-sm">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={`flex items-start gap-2 px-3.5 py-2.5 rounded-lg shadow-md text-[13px] ${
              t.kind === "error"
                ? "bg-red-500 text-white"
                : "bg-foreground text-background"
            }`}
          >
            <span className="flex-1">{t.message}</span>
            <button
              onClick={() => {}}
              className="shrink-0 opacity-70 hover:opacity-100"
              aria-hidden
            >
              <X className="size-3.5" />
            </button>
          </div>
        ))}
      </div>

      <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
        {/* Header with dropdown button */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary">
              <FileUp className="size-5" />
            </div>
            <div>
              <h3 className="text-base font-semibold">Knowledge Base</h3>
              <p className="text-[13px] text-muted-foreground">
                Upload documents or crawl your website. The agent uses this content to answer questions.
              </p>
            </div>
          </div>
          <div className="relative" ref={dropdownRef}>
            <button
              onClick={() => {
                if (atLimit && !crawlId) {
                  push(`Maximum of ${MAX_DOCS_PER_ORG} documents reached.`, "error");
                  return;
                }
                setDropdownOpen(!dropdownOpen);
              }}
              className="text-[13px] font-medium px-3 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity flex items-center gap-1.5 disabled:opacity-50"
            >
              <Upload className="size-4" />
              Add Knowledge
              <ChevronDown className={`size-3.5 transition-transform ${dropdownOpen ? "rotate-180" : ""}`} />
            </button>
            {dropdownOpen && (
              <div className="absolute right-0 mt-1.5 w-52 bg-card border border-border rounded-xl shadow-lg overflow-hidden z-50 animate-in fade-in slide-in-from-top-2 duration-150">
                <button
                  onClick={() => {
                    setDropdownOpen(false);
                    setModalMode("upload");
                  }}
                  disabled={atLimit}
                  className="w-full flex items-center gap-3 px-3.5 py-2.5 text-left hover:bg-secondary/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <div className="flex items-center justify-center size-8 rounded-lg bg-red-500/10 text-red-500 shrink-0">
                    <FileUp className="size-4" />
                  </div>
                  <div>
                    <div className="text-[13px] font-medium">Upload Document</div>
                    <div className="text-[11px] text-muted-foreground">PDF, DOCX, XLSX, CSV, TXT, MD</div>
                  </div>
                </button>
                <div className="h-px bg-border mx-3" />
                <button
                  onClick={() => {
                    setDropdownOpen(false);
                    setModalMode("crawl");
                  }}
                  className="w-full flex items-center gap-3 px-3.5 py-2.5 text-left hover:bg-secondary/50 transition-colors"
                >
                  <div className="flex items-center justify-center size-8 rounded-lg bg-blue-500/10 text-blue-500 shrink-0">
                    <Globe className="size-4" />
                  </div>
                  <div>
                    <div className="text-[13px] font-medium">Crawl Website</div>
                    <div className="text-[11px] text-muted-foreground">Extract pages from your site</div>
                  </div>
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Doc count */}
        <div className="flex items-center justify-between text-[12px] mb-3">
          <span className="text-muted-foreground">
            {docList.length} / {MAX_DOCS_PER_ORG} documents
          </span>
          {atLimit && (
            <span className="text-amber-500 font-medium">
              ⚠ Max reached — delete a document to upload another.
            </span>
          )}
        </div>

        {/* Document list */}
        {docsLoading ? (
          <div className="flex items-center gap-2 text-muted-foreground text-sm py-6">
            <Loader2 className="size-4 animate-spin" /> Loading documents…
          </div>
        ) : docsError ? (
          <div className="flex items-center gap-2 text-red-500 text-sm py-6">
            <AlertCircle className="size-4" /> {docsError}
          </div>
        ) : docList.length === 0 ? (
          <div className="text-center py-8 border border-dashed border-border rounded-lg">
            <p className="text-muted-foreground text-sm">No knowledge sources yet.</p>
            <p className="text-muted-foreground/60 text-xs mt-1">
              Click &ldquo;Add Knowledge&rdquo; to upload a document or crawl your website.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            {docList.map((doc) => (
              <DocumentCard
                key={doc.id}
                doc={doc}
                deleting={pendingDelete === doc.id}
                onDelete={() => onDelete(doc)}
              />
            ))}
          </div>
        )}

        {/* Crawl status */}
        {crawlId && (
          <div className="mt-4 border border-border rounded-lg p-3 bg-background space-y-2">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[11px] text-muted-foreground uppercase tracking-wide">Crawl ID</p>
                <p className="text-[12px] font-mono truncate text-foreground">{crawlId}</p>
              </div>
              <span
                className={`text-[11px] font-medium px-2.5 py-1 rounded-full ${crawlStatusColor} capitalize`}
              >
                {crawlStatusText}
              </span>
            </div>
            {crawlStatus?.pages_crawled != null && (
              <p className="text-[12px] text-muted-foreground">
                Pages crawled so far: <span className="font-medium text-foreground">{crawlStatus.pages_crawled}</span>
              </p>
            )}
            {(crawlStatus?.stored_pages != null || websiteSummary) && (
              <p className="text-[12px] text-muted-foreground">
                Saved in knowledge base:{" "}
                <span className="font-medium text-foreground">
                  {crawlStatus?.stored_pages ?? websiteSummary?.stored_pages ?? 0} pages
                  ({crawlStatus?.stored_chunks ?? websiteSummary?.stored_chunks ?? 0} chunks)
                </span>
              </p>
            )}
            {crawlStatusText.toLowerCase() === "completed" && (
              <p className="text-[12px] text-green-500">
                ✓ Crawl complete — content is now searchable by the agent.
              </p>
            )}
            {crawlStatusText.toLowerCase() === "failed" && (
              <p className="text-[12px] text-red-500">
                ✗ Crawl failed. {((crawlStatus?.stored_pages ?? websiteSummary?.stored_pages ?? 0) > 0)
                  ? "Pages already saved stay in the knowledge base."
                  : "Check the URL and try again."}
              </p>
            )}
          </div>
        )}

        {/* Persisted website data (DB truth — never auto-deleted) */}
        {((websiteSummary?.stored_pages ?? 0) > 0 || (websiteSummary?.website_url && !crawlId)) && (
          <div className="mt-3 border border-border rounded-lg p-3 bg-background space-y-2">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-[11px] text-muted-foreground uppercase tracking-wide">Crawled website</p>
                <p className="text-[12px] font-mono truncate text-foreground">
                  {websiteSummary?.website_url ?? "—"}
                </p>
                <p className="text-[12px] text-muted-foreground mt-1">
                  Saved: <span className="font-medium text-foreground">{websiteSummary?.stored_pages} pages ({websiteSummary?.stored_chunks} chunks)</span>
                  {websiteSummary?.crawl_status && (
                    <span className="ml-2 capitalize">· {websiteSummary.crawl_status}</span>
                  )}
                </p>
              </div>
              <button
                onClick={onDeleteWebsite}
                disabled={pendingWebsiteDelete}
                className="inline-flex items-center gap-1.5 text-[12px] font-medium text-red-500 hover:text-red-600 disabled:opacity-50 shrink-0"
              >
                {pendingWebsiteDelete ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
                Delete
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Modal */}
      {modalMode && (
        <AddKnowledgeModal
          mode={modalMode}
          docCount={docList.length}
          onClose={() => setModalMode(null)}
          onUploadDone={async (filename, pages) => {
            push(`Uploaded "${filename}" (${pages} ${pages === 1 ? "page" : "pages"}).`);
            setModalMode(null);
            await refetchDocs();
          }}
          onCrawlStarted={(id, url) => {
            setCrawlId(id);
            push(`Crawl started for ${url}.`);
            setModalMode(null);
          }}
        />
      )}
    </>
  );
}
