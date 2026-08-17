"use client";

import { useState, useEffect, useRef } from "react";
import {
  Loader2, AlertCircle, FileText, Trash2, Upload,
  Globe, RefreshCw, X, FileUp,
} from "lucide-react";
import {
  useDocuments, useCrawlStatus, useAgentConfig,
  uploadPdfDocument, deletePdfDocument, startWebsiteCrawl,
  MAX_DOCS_PER_ORG,
  type DocumentListItem,
} from "@/hooks/use-dashboard";

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

// ─── Document uploader + list ───────────────────────────────────────────

function DocumentsSection({ push }: { push: (m: string, k?: Toast["kind"]) => void }) {
  const { data: docs, loading, error, refetch } = useDocuments();
  const [uploading, setUploading] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const docList = docs ?? [];
  const atLimit = docList.length >= MAX_DOCS_PER_ORG;

const SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"];
const ACCEPT_TYPES = SUPPORTED_EXTENSIONS.join(",");

  const onPickFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = "";

    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!SUPPORTED_EXTENSIONS.includes(ext)) {
      push("Unsupported file type. Supported: PDF, DOCX, XLSX, CSV, TXT, MD", "error");
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      push("File exceeds 25MB limit.", "error");
      return;
    }
    if (atLimit) {
      push(`Maximum of ${MAX_DOCS_PER_ORG} documents reached.`, "error");
      return;
    }

    setUploading(true);
    try {
      const res = await uploadPdfDocument(file);
      push(`Uploaded "${res.filename}" (${res.pages} ${res.pages === 1 ? "page" : "pages"}).`);
      await refetch();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Upload failed";
      push(msg, "error");
    } finally {
      setUploading(false);
    }
  };

  const onDelete = async (doc: DocumentListItem) => {
    if (!confirm(`Delete "${doc.filename}"? This removes it from the agent's knowledge base.`)) return;
    setPendingDelete(doc.id);
    try {
      await deletePdfDocument(doc.id);
      push(`Deleted "${doc.filename}".`);
      await refetch();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Delete failed";
      push(msg, "error");
    } finally {
      setPendingDelete(null);
    }
  };

  return (
    <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary">
            <FileUp className="size-5" />
          </div>
          <div>
            <h3 className="text-base font-semibold">Document Knowledge Base</h3>
            <p className="text-[13px] text-muted-foreground">
              Upload up to {MAX_DOCS_PER_ORG} documents (PDF, DOCX, XLSX, CSV, TXT, MD). The agent answers questions from this content.
            </p>
          </div>
        </div>
        <button
          onClick={() => inputRef.current?.click()}
          disabled={uploading || atLimit}
          className="text-[13px] font-medium px-3 py-2 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity flex items-center gap-1.5 disabled:opacity-50"
        >
          {uploading ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Upload className="size-4" />
          )}
          Upload Document
        </button>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_TYPES}
          className="hidden"
          onChange={onPickFile}
        />
      </div>

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

      {loading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm py-6">
          <Loader2 className="size-4 animate-spin" /> Loading documents…
        </div>
      ) : error ? (
        <div className="flex items-center gap-2 text-red-500 text-sm py-6">
          <AlertCircle className="size-4" /> {error}
        </div>
      ) : docList.length === 0 ? (
        <div className="text-center py-8 border border-dashed border-border rounded-lg">
          <p className="text-muted-foreground text-sm">No documents uploaded yet.</p>
          <p className="text-muted-foreground/60 text-xs mt-1">
            Click &ldquo;Upload Document&rdquo; to feed the agent new knowledge.
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
    </div>
  );
}

// ─── Website crawler section ─────────────────────────────────────────────

// Poll every 30s: Firecrawl's default plan rate limit is 6 req/min, so a
// faster cadence exhausts the quota (each poll is one API call).
const CRAWL_POLL_MS = 30000;

function WebsiteCrawlerSection({ push, onCrawlId }: {
  push: (m: string, k?: Toast["kind"]) => void;
  onCrawlId: (id: string | null) => void;
}) {
  const [url, setUrl] = useState("");
  const [starting, setStarting] = useState(false);
  const [crawlId, setCrawlId] = useState<string | null>(null);
  const { data: status, refetch } = useCrawlStatus(crawlId);
  const { data: agentConfig } = useAgentConfig();

  // Restore in-progress crawl state after page refresh.
  // Skip terminal states (completed/failed/cancelled) so an expired
  // Firecrawl job does not get resurrected and polled forever.
  useEffect(() => {
    if (!agentConfig) return;
    if (agentConfig.website_url && !crawlId) {
      setUrl(agentConfig.website_url);
    }
    const terminal = ["completed", "failed", "cancelled"];
    const storedStatus = (agentConfig.crawl_status || "").toLowerCase();
    if (agentConfig.crawl_id && !crawlId && !terminal.includes(storedStatus)) {
      setCrawlId(agentConfig.crawl_id);
    }
  }, [agentConfig]);

  useEffect(() => {
    onCrawlId(crawlId);
  }, [crawlId, onCrawlId]);

  // Poll while crawl is active
  useEffect(() => {
    if (!crawlId) return;
    const isDone = (status?.status || "").toLowerCase();
    if (isDone === "completed" || isDone === "failed" || isDone === "cancelled") return;
    const t = setInterval(() => { refetch(); }, CRAWL_POLL_MS);
    return () => clearInterval(t);
  }, [crawlId, status, refetch]);

  const onCrawl = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!url.trim()) return;
    setStarting(true);
    try {
      const res = await startWebsiteCrawl(url.trim());
      setCrawlId(res.crawl_id);
      push(`Crawl started for ${url.trim()}.`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Crawl failed to start";
      push(msg, "error");
    } finally {
      setStarting(false);
    }
  };

  const statusText = status?.status || (crawlId ? "starting" : "idle");
  const statusBadgeColor =
    statusText.toLowerCase() === "completed"
      ? "text-green-500 bg-green-500/10"
      : statusText.toLowerCase() === "failed"
      ? "text-red-500 bg-red-500/10"
      : "text-blue-500 bg-blue-500/10";

  return (
    <div className="bg-card border border-border rounded-xl p-4 sm:p-6">
      <div className="flex items-center gap-3 mb-4">
        <div className="flex items-center justify-center size-9 rounded-lg bg-primary/10 text-primary">
          <Globe className="size-5" />
        </div>
        <div>
          <h3 className="text-base font-semibold">Website Knowledge Base</h3>
          <p className="text-[13px] text-muted-foreground">
            Crawl your website. The agent will use crawled pages to answer questions.
          </p>
        </div>
      </div>

      <form onSubmit={onCrawl} className="flex flex-col sm:flex-row gap-2">
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://your-website.com"
          required
          disabled={starting}
          className="flex-1 bg-background border border-border text-foreground px-3.5 py-2.5 rounded-md text-[13px] font-sans focus:outline-none focus:border-primary focus:ring-1 focus:ring-primary transition-all"
        />
        <button
          type="submit"
          disabled={starting || !url.trim()}
          className="text-[13px] font-medium px-4 py-2.5 rounded-md bg-primary text-primary-foreground hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5 disabled:opacity-50"
        >
          {starting ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <RefreshCw className="size-4" />
          )}
          Start Crawl
        </button>
      </form>

      {crawlId && (
        <div className="mt-4 border border-border rounded-lg p-3 bg-background space-y-2">
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[11px] text-muted-foreground uppercase tracking-wide">Crawl ID</p>
              <p className="text-[12px] font-mono truncate text-foreground">{crawlId}</p>
            </div>
            <span
              className={`text-[11px] font-medium px-2.5 py-1 rounded-full ${statusBadgeColor} capitalize`}
            >
              {statusText}
            </span>
          </div>
          {status?.pages_crawled != null && (
            <p className="text-[12px] text-muted-foreground">
              Pages crawled so far: <span className="font-medium text-foreground">{status.pages_crawled}</span>
            </p>
          )}
          {(status?.status || "").toLowerCase() === "completed" && (
            <p className="text-[12px] text-green-500">
              ✓ Crawl complete — content is now searchable by the agent.
            </p>
          )}
          {(status?.status || "").toLowerCase() === "failed" && (
            <p className="text-[12px] text-red-500">
              ✗ Crawl failed. Check the URL and try again.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Top-level section used by agent page ────────────────────────────────

export function KnowledgeBaseSection() {
  const { toasts, push } = useToasts();
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

      <DocumentsSection push={push} />
      <WebsiteCrawlerSection push={push} onCrawlId={() => {}} />
    </>
  );
}
