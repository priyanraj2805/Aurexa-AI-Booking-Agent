"""Endpoints for document upload (PDF, DOCX, XLSX, CSV, TXT, MD) and website
crawling (vectorless RAG data sources).

All org isolation is enforced by deriving ``org_id`` from the authenticated user
same as every other dashboard endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from typing import List, Optional
from pydantic import BaseModel, HttpUrl, Field, ValidationError
import logging
import json as _json
import os
import uuid as _uuid
from pathlib import Path

from app.db.database import get_db
from app.core.config import settings
from app.models.config import OrgConfig
from app.models.user import User
from app.api.v1.endpoints.auth import get_current_user, deny_impersonation
from app.services.document_processor import (
    SUPPORTED_EXTENSIONS,
    process_document,
    compute_content_hash,
)
from app.services.firecrawl_service import get_firecrawl_service
from app.services.retrieval_service import (
    store_document_chunks,
    delete_document_chunks,
    delete_website_chunks,
    save_business_profile,
    get_business_profile,
    upsert_source_context,
    get_stored_source_context,
    delete_source_context_entry,
)
from app.engine.knowledge_profile import sanitize_source_context

logger = logging.getLogger(__name__)

MAX_DOCS_PER_ORG = 10
MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB per file

router = APIRouter(tags=["Documents"])


# ─── Response Schemas ──────────────────────────────────────────────────────


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    pages: int
    status: str


class DocumentListItem(BaseModel):
    id: str
    filename: str
    pages: int
    status: str
    uploaded_at: Optional[str] = None


class CrawlRequest(BaseModel):
    url: str
    # Optional context collected from the admin before starting the crawl.
    source_description: Optional[str] = Field(default=None, max_length=2000)
    source_category: Optional[str] = Field(default=None, max_length=100)
    pages_that_matter: Optional[str] = Field(default=None, max_length=2000)
    not_covered: Optional[str] = Field(default=None, max_length=2000)
    questions_to_answer: Optional[List[str]] = None


class CrawlResponse(BaseModel):
    crawl_id: str
    status: str
    message: Optional[str] = None


class CrawlStatusResponse(BaseModel):
    crawl_id: str
    status: str
    pages_crawled: Optional[int] = None
    # DB-truth counts (distinct stored pages / chunks for this crawl).
    stored_pages: Optional[int] = None
    stored_chunks: Optional[int] = None


class WebsiteSummaryResponse(BaseModel):
    website_url: Optional[str] = None
    crawl_id: Optional[str] = None
    crawl_status: Optional[str] = None
    crawl_started_at: Optional[str] = None
    crawl_completed_at: Optional[str] = None
    total_pages_crawled: Optional[int] = None
    # DB truth — what is actually stored and answerable right now.
    stored_pages: int = 0
    stored_chunks: int = 0


# ─── Helpers ───────────────────────────────────────────────────────────────


class BusinessProfilePayload(BaseModel):
    """Admin-collected 'About your business' context (knowledge wizard step 1)."""

    business_name: Optional[str] = Field(default=None, max_length=255)
    business_description: str = Field(min_length=1, max_length=2000)
    products_services: Optional[str] = Field(default=None, max_length=2000)
    target_customers: Optional[str] = Field(default=None, max_length=1000)
    key_policies: Optional[str] = Field(default=None, max_length=2000)
    answer_language_tone: Optional[str] = Field(default=None, max_length=300)


class BusinessProfileResponse(BaseModel):
    business_name: Optional[str] = None
    business_description: Optional[str] = None
    products_services: Optional[str] = None
    target_customers: Optional[str] = None
    key_policies: Optional[str] = None
    answer_language_tone: Optional[str] = None
    configured: bool = False


async def _count_uploaded_documents(db: AsyncSession, org_id) -> int:
    result = await db.execute(
        text(
            "SELECT COUNT(DISTINCT doc_id) FROM document_chunks "
            "WHERE org_id = :org_id AND source_type <> 'website'"
        ),
        {"org_id": str(org_id)},
    )
    return int(result.scalar() or 0)


# ─── PDF Endpoints ──────────────────────────────────────────────────────────


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    source_context: Optional[str] = Form(
        default=None,
        description=(
            'Optional JSON object of admin-collected context, e.g. '
            '{"source_description": "...", "source_category": "...", '
            '"questions_to_answer": ["..."]}'
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
):
    """Upload a document (PDF, DOCX, XLSX, CSV, TXT, MD; max 10 per org).
    Chunks are stored for BM25 search."""
    org_id = user.org_id

    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported file type. Supported formats: "
                "PDF, DOCX, XLSX, CSV, TXT, MD"
            ),
        )

    existing_count = await _count_uploaded_documents(db, org_id)
    if existing_count >= MAX_DOCS_PER_ORG:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum of {MAX_DOCS_PER_ORG} documents per organization reached",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(raw) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {MAX_PDF_BYTES // (1024 * 1024)}MB limit",
        )

    content_hash = compute_content_hash(raw)

    # Reject duplicate uploads for this org
    dup = await db.execute(
        text(
            "SELECT 1 FROM document_chunks WHERE org_id = :org_id "
            "AND source_type <> 'website' AND metadata->>'content_hash' = :h LIMIT 1"
        ),
        {"org_id": str(org_id), "h": content_hash},
    )
    if dup.first() is not None:
        raise HTTPException(status_code=409, detail="This document has already been uploaded")

    filename = file.filename or "document"
    try:
        chunks = await process_document(raw, filename)
    except Exception as exc:
        logger.error(f"Document processing failed for {filename}: {exc}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"Document processing failed: {exc}")

    if not chunks:
        raise HTTPException(status_code=422, detail="No extractable text found in document")

    # Parse + sanitize the optional admin-collected source context.
    context_error = HTTPException(
        status_code=400,
        detail="Invalid source_context: expected a JSON object with string fields",
    )
    source_ctx: dict = {}
    if source_context:
        try:
            parsed = _json.loads(source_context)
        except (TypeError, ValueError):
            raise context_error
        questions = parsed.get("questions_to_answer") if isinstance(parsed, dict) else None
        if not isinstance(parsed, dict) or (
            questions is not None and not isinstance(questions, list)
        ):
            raise context_error
        source_ctx = sanitize_source_context(
            description=parsed.get("source_description"),
            category=parsed.get("source_category"),
            questions_to_answer=questions,
        )

    source_type = str(chunks[0].get("source_type") or "pdf")
    page_count = len({c.get("page_number") for c in chunks if c.get("page_number")})

    doc_id = await store_document_chunks(
        db=db,
        org_id=str(org_id),
        chunks=chunks,
        source_type=source_type,
        source_url=None,
        doc_id=None,
    )

    # Stamp filename + content_hash (+ admin-collected context) on every
    # chunk's metadata for listing/dedup and RAG source labeling.
    metadata_update: dict = {
        "filename": filename,
        "content_hash": content_hash,
    }
    metadata_update.update(source_ctx)
    await db.execute(
        text(
            "UPDATE document_chunks "
            "SET metadata = COALESCE(metadata, '{}'::jsonb) || "
            "  CAST(:meta AS JSONB) "
            "WHERE org_id = :org_id AND doc_id = :doc_id"
        ),
        {
            "org_id": str(org_id),
            "doc_id": doc_id,
            "meta": _json.dumps(metadata_update),
        },
    )

    await db.commit()

    # Persist doc reference on org_configs.uploaded_docs (JSONB array) for the admin UI
    await _append_doc_to_org_config(db, org_id, doc_id, filename, page_count)

    # Remember the admin-collected context keyed by doc id so it survives
    # listing/restarts and can be re-stamped onto future ingests.
    if source_ctx:
        await upsert_source_context(db, str(org_id), str(doc_id), source_ctx)
        await db.commit()

    return DocumentUploadResponse(
        id=str(doc_id),
        filename=filename,
        pages=page_count,
        status="processed",
    )


@router.get("/documents", response_model=List[DocumentListItem])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """List all documents uploaded for the authenticated org."""
    org_id = user.org_id
    result = await db.execute(
        text(
            f"""
            SELECT
                doc_id AS id,
                metadata->>'filename' AS filename,
                COUNT(DISTINCT page_number) AS pages,
                MIN(created_at) AS uploaded_at
            FROM document_chunks
            WHERE org_id = :org_id AND source_type <> 'website'
            GROUP BY doc_id, metadata->>'filename'
            ORDER BY uploaded_at DESC
            """
        ),
        {"org_id": str(org_id)},
    )
    rows = result.mappings().all()
    return [
        DocumentListItem(
            id=str(r["id"]),
            filename=r["filename"] or "untitled.pdf",
            pages=int(r["pages"] or 0),
            status="processed",
            uploaded_at=str(r["uploaded_at"]) if r["uploaded_at"] else None,
        )
        for r in rows
    ]


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
):
    """Delete a document and all its chunks for this org.

    Website-crawl batches are protected here: crawled data is removed
    ONLY via DELETE /website (explicit admin action), never as a side
    effect of document deletion.
    """
    org_id = user.org_id

    if str(doc_id).startswith("website:"):
        raise HTTPException(
            status_code=400,
            detail="Crawled website data can only be deleted via DELETE /website",
        )
    _web_check = await db.execute(
        text(
            "SELECT 1 FROM document_chunks WHERE org_id = :org_id "
            "AND doc_id = :doc_id AND source_type = 'website' LIMIT 1"
        ),
        {"org_id": str(org_id), "doc_id": str(doc_id)},
    )
    if _web_check.first() is not None:
        raise HTTPException(
            status_code=400,
            detail="Crawled website data can only be deleted via DELETE /website",
        )

    deleted = await delete_document_chunks(db, str(org_id), doc_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Document not found")

    await _remove_doc_from_org_config(db, org_id, doc_id)
    await delete_source_context_entry(db, str(org_id), doc_id)
    await db.commit()
    return {"success": True, "deleted_chunks": deleted, "doc_id": doc_id}


@router.delete("/website")
async def delete_website_data(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
):
    """Delete all crawled website data for this org."""
    org_id = user.org_id

    deleted = await delete_website_chunks(db, str(org_id))

    # Clear website-related fields on org_config
    await db.execute(
        text(
            "UPDATE org_configs SET website_url = NULL, crawl_status = NULL, "
            "crawl_started_at = NULL, crawl_completed_at = NULL, total_pages_crawled = NULL "
            "WHERE org_id = :org_id"
        ),
        {"org_id": str(org_id)},
    )
    await delete_source_context_entry(db, str(org_id), "website")
    await db.commit()
    return {"success": True, "deleted_chunks": deleted}


# ─── Business Profile (knowledge wizard step 1) ────────────────────────────


@router.get("/business-profile", response_model=BusinessProfileResponse)
async def get_business_profile_endpoint(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the admin-collected business profile for prefilling the wizard."""
    profile = await get_business_profile(db, str(user.org_id))
    return BusinessProfileResponse(
        business_name=profile.get("business_name"),
        business_description=profile.get("business_description"),
        products_services=profile.get("products_services"),
        target_customers=profile.get("target_customers"),
        key_policies=profile.get("key_policies"),
        answer_language_tone=profile.get("answer_language_tone"),
        configured=bool(profile),
    )


@router.put("/business-profile", response_model=BusinessProfileResponse)
async def save_business_profile_endpoint(
    payload: BusinessProfilePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = Depends(deny_impersonation),
):
    """Persist the 'About your business' context collected by the wizard.

    Stored on org_configs.knowledge_context.business_profile and injected
    into the agent system prompt for every conversation turn.
    """
    org_id = str(user.org_id)

    # Fall back to the org's existing business name when not supplied.
    profile_data = payload.model_dump(exclude_none=True)
    if not profile_data.get("business_name"):
        result = await db.execute(
            text("SELECT business_name FROM org_configs WHERE org_id = :org_id"),
            {"org_id": org_id},
        )
        row = result.mappings().first()
        stored_name = str(row["business_name"]).strip() if row and row["business_name"] else None
        if stored_name:
            profile_data["business_name"] = stored_name[:255]

    await save_business_profile(db, org_id, profile_data)
    await db.commit()

    return BusinessProfileResponse(
        business_name=profile_data.get("business_name"),
        business_description=profile_data.get("business_description"),
        products_services=profile_data.get("products_services"),
        target_customers=profile_data.get("target_customers"),
        key_policies=profile_data.get("key_policies"),
        answer_language_tone=profile_data.get("answer_language_tone"),
        configured=True,
    )


# ─── Website Crawl Endpoints ───────────────────────────────────────────────


@router.get("/website", response_model=WebsiteSummaryResponse)
async def get_website_summary(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """DB-truth summary of the crawled website for this org.

    Crawled pages persist until the admin explicitly calls DELETE /website —
    no status transition, poll, webhook, or background job removes them.
    """
    from app.services.retrieval_service import website_crawl_stats

    row = (
        await db.execute(
            text(
                "SELECT website_url, crawl_id, crawl_status, "
                "CAST(crawl_started_at AS TEXT) AS started, "
                "CAST(crawl_completed_at AS TEXT) AS completed, "
                "total_pages_crawled FROM org_configs WHERE org_id = :org_id"
            ),
            {"org_id": str(user.org_id)},
        )
    ).mappings().first() or {}
    try:
        stats = await website_crawl_stats(db, str(user.org_id))
    except Exception:
        stats = {"stored_pages": 0, "stored_chunks": 0}
    return WebsiteSummaryResponse(
        website_url=row.get("website_url"),
        crawl_id=row.get("crawl_id"),
        crawl_status=row.get("crawl_status"),
        crawl_started_at=row.get("started"),
        crawl_completed_at=row.get("completed"),
        total_pages_crawled=row.get("total_pages_crawled"),
        stored_pages=stats["stored_pages"],
        stored_chunks=stats["stored_chunks"],
    )


@router.post("/website/crawl", response_model=CrawlResponse)
async def crawl_website(
    payload: CrawlRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Trigger Firecrawl website crawl.

    If ``PUBLIC_API_URL`` (or ``PUBLIC_API_BASE``) is configured, Firecrawl
    will POST results to ``{public_url}/api/v1/firecrawl/webhook?org_id=...``
    when the crawl completes. Otherwise the crawl starts without a webhook
    and the admin UI polls ``/website/crawl/status/{crawl_id}`` for results.
    """
    org_id = user.org_id

    # Normalize URL — prepend https:// if missing
    raw_url = str(payload.url).strip()
    if not raw_url.startswith(("http://", "https://")):
        raw_url = "https://" + raw_url

    public_api_url = (
        getattr(settings, "PUBLIC_API_URL", "")
        or os.getenv("PUBLIC_API_BASE", "")
        or os.getenv("NEXT_PUBLIC_API_BASE", "")
    )
    webhook_url: Optional[str] = None
    if public_api_url:
        base = str(public_api_url).rstrip("/")
        # Strip trailing /api/v1 if present — env var is sometimes set as
        # "<root>/api/v1" (full API base) and sometimes as just "<root>".
        # We always append /api/v1/... ourselves below.
        if base.endswith("/api/v1"):
            base = base[:-7]
        webhook_url = f"{base}/api/v1/firecrawl/webhook?org_id={str(org_id)}"

    firecrawl = get_firecrawl_service()
    try:
        crawl_id = await firecrawl.crawl_website(
            url=raw_url,
            webhook_url=webhook_url,
        )
        if not crawl_id:
            raise HTTPException(status_code=502, detail="Firecrawl returned no crawl id")

        # Persist crawl context on org_configs for admin UI status
        await _set_crawl_state(
            db, str(org_id), url=raw_url,
            crawl_id=crawl_id, status="started",
        )
        # Store the admin-collected context so both ingest paths (webhook +
        # status polling) can stamp it onto chunk metadata.
        crawl_ctx = sanitize_source_context(
            description=payload.source_description,
            category=payload.source_category,
            questions_to_answer=payload.questions_to_answer,
            extra={
                "pages_that_matter": payload.pages_that_matter or "",
                "not_covered": payload.not_covered or "",
            },
        )
        if crawl_ctx:
            await upsert_source_context(db, str(org_id), "website", crawl_ctx)
        await db.commit()
        msg = (
            "Crawl started; results will be ingested automatically via webhook."
            if webhook_url
            else "Crawl started (no webhook configured). Use /website/crawl/status/{crawl_id} to poll for completion."
        )
        return CrawlResponse(
            crawl_id=crawl_id,
            status="started",
            message=msg,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Website crawl start failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/website/crawl/status/{crawl_id}", response_model=CrawlStatusResponse)
async def get_crawl_status(
    crawl_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Check Firecrawl crawl status by crawl_id.

    When the crawl completes, this endpoint also **ingests** the results into
    ``document_chunks`` so the admin UI does not need a separate download step.
    """
    firecrawl = get_firecrawl_service()
    try:
        result = await firecrawl.get_crawl_status(crawl_id)
        if not isinstance(result, dict):
            return CrawlStatusResponse(crawl_id=crawl_id, status=str(result))

        status_str = str(result.get("status") or "unknown")
        pages_data = result.get("data") or []
        total = result.get("total") or len(pages_data)

        # Persist terminal failure (e.g. expired Firecrawl job) so the UI
        # stops polling and a page refresh does not resurrect it.
        # Stored chunks are NEVER touched here — a late failure only flips
        # the status; the counts below keep reflecting what is in the DB.
        if status_str in ("failed", "cancelled"):
            await _set_crawl_state(db, str(user.org_id), status=status_str)
            await db.commit()

        # If the crawl completed and we have data, ingest it (idempotent:
        # pages already stored for this crawl are skipped, so repeated
        # polls never duplicate chunks).
        if status_str == "completed" and pages_data and db:
            from app.services.document_processor import chunk_text
            from app.services.retrieval_service import (
                store_document_chunks,
                website_crawl_doc_id,
                website_crawl_stats,
                website_page_key,
                website_pages_stored,
            )

            # Re-attach admin-collected context to every ingested chunk.
            website_ctx = await get_stored_source_context(db, str(user.org_id), "website")
            all_chunks = []
            for page in pages_data:
                if not isinstance(page, dict):
                    continue
                markdown = page.get("markdown") or page.get("html") or ""
                if not markdown:
                    continue
                url = page.get("url") or page.get("sourceURL") or ""
                page_meta = page.get("metadata") or {}
                title = ""
                if isinstance(page_meta, dict):
                    title = page_meta.get("title") or page_meta.get("ogTitle") or ""
                for chunk in chunk_text(markdown):
                    all_chunks.append({
                        "content": chunk,
                        "source_type": "website",
                        "source_url": url,
                        "metadata": {
                            **website_ctx,
                            "title": title,
                            "crawl_id": crawl_id,
                        },
                    })

            already = await website_pages_stored(db, str(user.org_id), str(crawl_id))
            fresh = []
            for c in all_chunks:
                key = website_page_key(
                    c.get("source_url"), (c.get("metadata") or {}).get("title")
                )
                if key and key not in already:
                    already.add(key)
                    fresh.append(c)
            if fresh:
                await store_document_chunks(
                    db,
                    org_id=str(user.org_id),
                    chunks=fresh,
                    source_type="website",
                    doc_id=website_crawl_doc_id(str(crawl_id)),
                )
            await _set_crawl_state(
                db, str(user.org_id), status="completed",
            )
            # Honest count: distinct pages actually stored, not payload size.
            stats = await website_crawl_stats(db, str(user.org_id), str(crawl_id))
            await db.execute(
                text(
                    "UPDATE org_configs SET total_pages_crawled = :t "
                    "WHERE org_id = :org_id"
                ),
                {"t": stats["stored_pages"], "org_id": str(user.org_id)},
            )
            await db.commit()
            logger.info(
                f"Ingested {len(fresh)}/{len(all_chunks)} new chunks "
                f"(stored_pages={stats['stored_pages']}) for crawl {crawl_id}"
            )

        # DB-truth counts so the UI never shows 0/failed-looks-deleted
        # while pages are safely stored.
        stored_pages, stored_chunks = 0, 0
        try:
            from app.services.retrieval_service import website_crawl_stats as _stats

            _s = await _stats(db, str(user.org_id), str(crawl_id))
            stored_pages, stored_chunks = _s["stored_pages"], _s["stored_chunks"]
        except Exception:
            pass
        return CrawlStatusResponse(
            crawl_id=crawl_id,
            status=status_str,
            pages_crawled=total,
            stored_pages=stored_pages,
            stored_chunks=stored_chunks,
        )
    except Exception as exc:
        logger.error(f"Crawl status fetch failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ─── OrgConfig side-state helpers ───────────────────────────────────────────


async def _append_doc_to_org_config(
    db: AsyncSession, org_id, doc_id: str, filename: str, pages: int
) -> None:
    result = await db.execute(
        select(OrgConfig).where(OrgConfig.org_id == org_id)
    )
    cfg = result.scalars().first()
    if not cfg:
        return
    docs = list(cfg.uploaded_docs or [])
    docs.append({
        "doc_id": doc_id,
        "filename": filename,
        "pages": pages,
    })
    cfg.uploaded_docs = docs
    await db.flush()


async def _remove_doc_from_org_config(db: AsyncSession, org_id, doc_id: str) -> None:
    result = await db.execute(
        select(OrgConfig).where(OrgConfig.org_id == org_id)
    )
    cfg = result.scalars().first()
    if not cfg:
        return
    docs = [d for d in (cfg.uploaded_docs or []) if d.get("doc_id") != doc_id]
    cfg.uploaded_docs = docs
    await db.flush()


async def _set_crawl_state(
    db: AsyncSession,
    org_id: str,
    *,
    url: Optional[str] = None,
    crawl_id: Optional[str] = None,
    status: str = "started",
) -> None:
    """Persist crawl state on org_configs so the admin UI can restore it
    after a page refresh. All fields are upserted independently."""
    sets = []
    params: dict = {"org_id": str(org_id)}
    if url is not None:
        sets.append("website_url = :url")
        params["url"] = url
    if crawl_id is not None:
        sets.append("crawl_id = :crawl_id")
        params["crawl_id"] = crawl_id
    sets.append("crawl_status = :status")
    params["status"] = status
    if status == "started":
        sets.append("crawl_started_at = NOW()")
        sets.append("crawl_completed_at = NULL")
    elif status in ("completed", "failed", "cancelled"):
        sets.append("crawl_completed_at = NOW()")
    if not sets:
        return
    await db.execute(
        text(
            "UPDATE org_configs SET " + ", ".join(sets) + " WHERE org_id = :org_id"
        ),
        params,
    )
    await db.flush()


# Unused imports kept for clarity in case of future additions.
_ = (Optional, _uuid)
