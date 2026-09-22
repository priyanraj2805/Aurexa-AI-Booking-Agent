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
from typing import Optional

from app.db.database import AsyncSessionLocal
from app.services.retrieval_service import store_document_chunks, get_stored_source_context
from app.services.pdf_processor import chunk_text

router = APIRouter(tags=["Firecrawl Webhook"])
logger = logging.getLogger(__name__)


async def _update_crawl_status(org_id: str, crawl_id: str, status: str,
                                 total_pages: Optional[int] = None) -> None:
    """Update org_configs crawl state when a terminal webhook fires.

    ``total_pages`` is only overwritten when explicitly provided — a late
    ``failed`` event must never zero out the count while stored pages exist.
    """
    from app.services.retrieval_service import website_crawl_stats

    async with AsyncSessionLocal() as db:
        sets = "crawl_status = :status, crawl_completed_at = NOW()"
        params: dict = {
            "status": status,
            "org_id": str(org_id),
            "crawl_id": crawl_id,
        }
        if total_pages is None and status in ("completed", "failed"):
            try:
                stats = await website_crawl_stats(db, str(org_id), crawl_id)
                total_pages = stats["stored_pages"]
            except Exception:
                total_pages = None
        if total_pages is not None:
            sets += ", total_pages_crawled = :total_pages"
            params["total_pages"] = total_pages
        await db.execute(
            text(
                f"UPDATE org_configs SET {sets} "
                "WHERE org_id = :org_id AND crawl_id = :crawl_id"
            ),
            params,
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
            from app.services.retrieval_service import (
                website_crawl_doc_id,
                website_crawl_stats,
                website_page_key,
                website_pages_stored,
            )

            async with AsyncSessionLocal() as db:
                # Re-attach admin-collected context to every ingested chunk.
                website_ctx = await get_stored_source_context(db, str(org_id), "website")
                for chunk in all_chunks:
                    chunk["metadata"] = {**website_ctx, **(chunk.get("metadata") or {})}
                # Idempotent ingest: skip pages already stored for this crawl
                # so redelivered webhooks never duplicate chunks. One stable
                # doc_id per crawl keeps the batch identifiable.
                already = await website_pages_stored(db, str(org_id), str(crawl_id))
                fresh = [
                    c
                    for c in all_chunks
                    if website_page_key(
                        c.get("source_url"), (c.get("metadata") or {}).get("title")
                    )
                    not in already
                ]
                for c in fresh:
                    already.add(
                        website_page_key(
                            c.get("source_url"), (c.get("metadata") or {}).get("title")
                        )
                    )
                doc_id = website_crawl_doc_id(str(crawl_id))
                if fresh:
                    await store_document_chunks(
                        db,
                        org_id=str(org_id),
                        chunks=fresh,
                        source_type="website",
                        doc_id=doc_id,
                    )
                stats = await website_crawl_stats(db, str(org_id), str(crawl_id))
                await db.execute(
                    text(
                        "UPDATE org_configs SET total_pages_crawled = :t "
                        "WHERE org_id = :org_id"
                    ),
                    {"t": stats["stored_pages"], "org_id": str(org_id)},
                )
                await db.commit()
                logger.info(
                    f"Stored {len(fresh)}/{len(all_chunks)} new chunks for crawl_id={crawl_id} "
                    f"event={event_type} doc_id={doc_id} "
                    f"(stored_pages={stats['stored_pages']})"
                )

        return {"success": True}

    except Exception as e:
        logger.error(f"Firecrawl webhook error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
