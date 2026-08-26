"""Firecrawl v4 webhook handler.

Firecrawl v4 fires **per-page** events during a crawl, not a single
``completed`` event. Each webhook call has this shape::

    {
      "success": true,
      "type": "crawl.page" | "crawl.completed" | "crawl.failed",
      "id": "<crawl_id>",
      "data": [ { "url": "...", "markdown": "...", "metadata": {...} } ],
      "metadata": { ... }
    }

For ``crawl.page`` events we ingest the single page's markdown into BM25.
For ``crawl.completed`` / ``crawl.failed`` events we persist the terminal
status back to org_configs so the admin UI sees completion.
"""

from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import text
import logging

from app.db.database import AsyncSessionLocal
from app.services.retrieval_service import store_document_chunks, get_stored_source_context
from app.services.pdf_processor import chunk_text

router = APIRouter(tags=["Firecrawl Webhook"])
logger = logging.getLogger(__name__)


async def _update_crawl_status(org_id: str, crawl_id: str, status: str,
                                 total_pages: int = 0) -> None:
    """Update org_configs crawl state when a terminal webhook fires."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE org_configs SET crawl_status = :status, "
                "crawl_completed_at = NOW(), "
                "total_pages_crawled = :total_pages "
                "WHERE org_id = :org_id AND crawl_id = :crawl_id"
            ),
            {
                "status": status,
                "total_pages": total_pages,
                "org_id": str(org_id),
                "crawl_id": crawl_id,
            },
        )
        await db.commit()


@router.post("/webhook")
async def firecrawl_webhook(request: Request):
    try:
        payload = await request.json()
        if not isinstance(payload, dict):
            logger.warning(f"Firecrawl webhook: unexpected payload type {type(payload).__name__}")
            return {"success": True}

        event_type = payload.get("type") or payload.get("event") or "unknown"
        crawl_id = payload.get("id") or payload.get("crawl_id")
        org_id = request.query_params.get("org_id")

        logger.info(
            f"Firecrawl webhook type={event_type} crawl_id={crawl_id} "
            f"org_id={org_id} keys={list(payload.keys())}"
        )

        if not org_id:
            logger.warning("Firecrawl webhook missing org_id; cannot ingest")
            return {"success": False, "error": "missing org_id"}

        if event_type == "crawl.failed":
            logger.error(f"Firecrawl crawl {crawl_id} failed: {payload.get('metadata')}")
            await _update_crawl_status(org_id, crawl_id, "failed")
            return {"success": True}

        if event_type == "crawl.completed":
            await _update_crawl_status(org_id, crawl_id, "completed")
            # still fall through to ingest any data in the payload

        if event_type not in ("crawl.page", "crawl.completed"):
            return {"success": True}

        # ``data`` is a list of documents for both per-page and final events
        pages = payload.get("data") or []
        if not isinstance(pages, list) or not pages:
            return {"success": True}

        all_chunks = []
        for page in pages:
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
                all_chunks.append(
                    {
                        "content": chunk,
                        "source_type": "website",
                        "source_url": url,
                        "metadata": {
                            "title": title,
                            "crawl_id": crawl_id,
                            "event_type": event_type,
                        },
                    }
                )

        if all_chunks:
            async with AsyncSessionLocal() as db:
                # Re-attach admin-collected context to every ingested chunk.
                website_ctx = await get_stored_source_context(db, str(org_id), "website")
                for chunk in all_chunks:
                    chunk["metadata"] = {**website_ctx, **(chunk.get("metadata") or {})}
                doc_id = await store_document_chunks(
                    db,
                    org_id=str(org_id),
                    chunks=all_chunks,
                    source_type="website",
                )
                await db.commit()
                logger.info(
                    f"Stored {len(all_chunks)} chunks for crawl_id={crawl_id} "
                    f"event={event_type} doc_id={doc_id}"
                )

        return {"success": True}

    except Exception as e:
        logger.error(f"Firecrawl webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
