"""Endpoints for PDF upload and website crawling (vectorless RAG data sources).

All org isolation is enforced by deriving ``org_id`` from the authenticated user
same as every other dashboard endpoint.
"""

from fastapi import APIRouter, Depends, HTTPException, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from typing import List, Optional
from pydantic import BaseModel, HttpUrl
import logging
import os
import uuid as _uuid

from app.db.database import get_db
from app.core.config import settings
from app.models.config import OrgConfig
from app.models.user import User
from app.api.v1.endpoints.auth import get_current_user
from app.services.pdf_processor import process_pdf, compute_content_hash
from app.services.firecrawl_service import get_firecrawl_service
from app.services.retrieval_service import store_document_chunks, delete_document_chunks

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
    url: HttpUrl


class CrawlResponse(BaseModel):
    crawl_id: str
    status: str
    message: Optional[str] = None


class CrawlStatusResponse(BaseModel):
    crawl_id: str
    status: str
    pages_crawled: Optional[int] = None


# ─── Helpers ───────────────────────────────────────────────────────────────


async def _count_uploaded_pdfs(db: AsyncSession, org_id) -> int:
    result = await db.execute(
        text(
            "SELECT COUNT(DISTINCT doc_id) FROM document_chunks "
            "WHERE org_id = :org_id AND source_type = 'pdf'"
        ),
        {"org_id": str(org_id)},
    )
    return int(result.scalar() or 0)


# ─── PDF Endpoints ──────────────────────────────────────────────────────────


@router.post("/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Upload a PDF document (max 10 PDFs per org). Chunks are stored for BM25 search."""
    org_id = user.org_id

    if not file or not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    existing_count = await _count_uploaded_pdfs(db, org_id)
    if existing_count >= MAX_DOCS_PER_ORG:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum of {MAX_DOCS_PER_ORG} PDFs per organization reached",
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
            "AND source_type = 'pdf' AND metadata->>'content_hash' = :h LIMIT 1"
        ),
        {"org_id": str(org_id), "h": content_hash},
    )
    if dup.first() is not None:
        raise HTTPException(status_code=409, detail="This PDF has already been uploaded")

    filename = file.filename or "document.pdf"
    try:
        chunks = await process_pdf(raw, filename)
    except Exception as exc:
        logger.error(f"PDF processing failed for {filename}: {exc}", exc_info=True)
        raise HTTPException(status_code=422, detail=f"PDF processing failed: {exc}")

    if not chunks:
        raise HTTPException(status_code=422, detail="No extractable text found in PDF")

    page_count = len({c.get("page_number") for c in chunks if c.get("page_number")})

    doc_id = await store_document_chunks(
        db=db,
        org_id=str(org_id),
        chunks=chunks,
        source_type="pdf",
        source_url=None,
        doc_id=None,
    )

    # Stash filename + content_hash on every chunk's metadata for listing/dedup
    await db.execute(
        text(
            "UPDATE document_chunks "
            "SET metadata = COALESCE(metadata, '{}'::jsonb) || "
            "  jsonb_build_object('filename', CAST(:filename AS text), "
            "                       'content_hash', CAST(:h AS text)) "
            "WHERE org_id = :org_id AND doc_id = :doc_id"
        ),
        {
            "org_id": str(org_id),
            "doc_id": doc_id,
            "filename": filename,
            "h": content_hash,
        },
    )

    await db.commit()

    # Persist doc reference on org_configs.uploaded_docs (JSONB array) for the admin UI
    await _append_doc_to_org_config(db, org_id, doc_id, filename, page_count)

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
    """List all PDF documents uploaded for the authenticated org."""
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
            WHERE org_id = :org_id AND source_type = 'pdf'
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
):
    """Delete a document and all its chunks for this org."""
    org_id = user.org_id

    deleted = await delete_document_chunks(db, str(org_id), doc_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Document not found")

    await _remove_doc_from_org_config(db, org_id, doc_id)
    await db.commit()
    return {"success": True, "deleted_chunks": deleted, "doc_id": doc_id}


# ─── Website Crawl Endpoints ───────────────────────────────────────────────


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
            url=str(payload.url),
            webhook_url=webhook_url,
        )
        if not crawl_id:
            raise HTTPException(status_code=502, detail="Firecrawl returned no crawl id")

        # Persist crawl context on org_configs for admin UI status
        await _set_crawl_state(
            db, str(org_id), url=str(payload.url),
            crawl_id=crawl_id, status="started",
        )
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
    user: User = Depends(get_current_user),
):
    """Check Firecrawl crawl status by crawl_id."""
    firecrawl = get_firecrawl_service()
    try:
        status = await firecrawl.get_crawl_status(crawl_id)
        if isinstance(status, dict):
            return CrawlStatusResponse(
                crawl_id=crawl_id,
                status=str(status.get("status") or "unknown"),
                pages_crawled=status.get("total"),
            )
        return CrawlStatusResponse(crawl_id=crawl_id, status=str(status))
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
    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    cfg = result.scalars().first()
    if not cfg:
        return
    if url is not None:
        cfg.website_url = url
    if crawl_id is not None:
        # store crawl_id in custom_prompts metadata? Better: dedicated column was added.
        await db.execute(
            text(
                "UPDATE org_configs SET website_url = :url, crawl_status = :status "
                "WHERE org_id = :org_id"
            ),
            {"url": url, "status": status, "org_id": org_id},
        )
    await db.flush()


# Unused imports kept for clarity in case of future additions.
_ = (Optional, _uuid)
