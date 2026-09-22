"""Firecrawl v4 service wrapper.

Uses firecrawl-py v4's AsyncV1FirecrawlApp (the modern async API).
"""

import asyncio
import logging
import re
import time
from typing import Any, Dict, Optional

from firecrawl.v1 import AsyncV1FirecrawlApp
from firecrawl.v1.client import V1ScrapeOptions

logger = logging.getLogger(__name__)

# Max retries for Firecrawl rate-limit (429) responses.
_RATE_LIMIT_MAX_RETRIES = 3

# In-memory TTL cache so repeated status polls don't each burn an API call.
# Entries are keyed by crawl_id and expire after the TTL.
_STATUS_CACHE_TTL_SECONDS = 20
_status_cache: Dict[str, Dict[str, Any]] = {}  # crawl_id -> {"_ts": float, **payload}


def _cache_get(crawl_id: str) -> Optional[Dict[str, Any]]:
    entry = _status_cache.get(crawl_id)
    if not entry:
        return None
    if time.monotonic() - entry.get("_ts", 0) > _STATUS_CACHE_TTL_SECONDS:
        _status_cache.pop(crawl_id, None)
        return None
    return {k: v for k, v in entry.items() if k != "_ts"}


def _cache_put(crawl_id: str, payload: Dict[str, Any]) -> None:
    entry = dict(payload)
    entry["_ts"] = time.monotonic()
    _status_cache[crawl_id] = entry


def _wait_for_rate_limit(message: str) -> int:
    """Extract 'retry after Ns' from a Firecrawl 429 message; default 60s."""
    match = re.search(r"retry after (\d+)s", message or "")
    return (int(match.group(1)) + 1) if match else 60


def _serialize_crawl_pages(data: Any) -> list:
    """Normalize V1 crawl page payloads to plain dicts.

    Accepts pydantic document objects or raw dicts and returns
    ``[{url, markdown, html, metadata{title}}]`` — the shape both ingest
    paths (webhook + status poll) already consume.
    """
    if not data:
        return []
    items = list(data) if isinstance(data, (list, tuple)) else [data]
    out = []
    for item in items:
        if isinstance(item, dict):
            meta = item.get("metadata") or {}
            if not isinstance(meta, dict):
                meta = {}
            out.append(
                {
                    "url": item.get("url") or item.get("sourceURL") or "",
                    "markdown": item.get("markdown") or "",
                    "html": item.get("html") or "",
                    "metadata": {
                        "title": meta.get("title") or meta.get("ogTitle") or "",
                    },
                }
            )
            continue
        meta = getattr(item, "metadata", None) or {}
        if not isinstance(meta, dict):
            try:
                meta = dict(meta)
            except Exception:
                meta = {}
        out.append(
            {
                "url": getattr(item, "url", None)
                or getattr(item, "sourceURL", None)
                or "",
                "markdown": getattr(item, "markdown", None) or "",
                "html": getattr(item, "html", None) or "",
                "metadata": {
                    "title": meta.get("title") or meta.get("ogTitle") or "",
                },
            }
        )
    return out


class FirecrawlService:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("FIRECRAWL_API_KEY not set")
        self.client: AsyncV1FirecrawlApp = AsyncV1FirecrawlApp(api_key=api_key)

    async def crawl_website(
        self,
        url: str,
        webhook_url: Optional[str] = None,
        limit: int = 100,
    ) -> str:
        """Start an async crawl. Returns the crawl ID (string).

        Retries on 429 (rate limit) after waiting for the quota reset, so a
        single user click succeeds even when the plan's req/min limit is
        exhausted.
        """
        scrape_options = V1ScrapeOptions(formats=["markdown"])
        kwargs: Dict[str, Any] = {
            "limit": limit,
            "scrape_options": scrape_options,
        }
        if webhook_url:
            kwargs["webhook"] = webhook_url

        last_error: Optional[Exception] = None
        for attempt in range(1, _RATE_LIMIT_MAX_RETRIES + 1):
            try:
                response = await self.client.async_crawl_url(url, **kwargs)
                crawl_id = getattr(response, "id", None) or str(response)
                return str(crawl_id)
            except Exception as e:
                last_error = e
                err = str(e)
                if "429" not in err:
                    logger.error(f"Firecrawl crawl start failed for {url}: {err}", exc_info=True)
                    raise
                wait = _wait_for_rate_limit(err)
                logger.warning(
                    f"Firecrawl rate limit on crawl start for {url} "
                    f"(attempt {attempt}/{_RATE_LIMIT_MAX_RETRIES}); "
                    f"waiting {wait}s before retry"
                )
                if attempt < _RATE_LIMIT_MAX_RETRIES:
                    await asyncio.sleep(wait)
        raise last_error

    async def get_crawl_status(self, crawl_id: str) -> Dict[str, Any]:
        """Return crawl status as a plain dict for the API response.

        Results are cached for a short TTL so the admin UI's polling loop
        does not burn through the plan's req/min quota.
        """
        cached = _cache_get(crawl_id)
        if cached is not None:
            return cached
        try:
            status = await self.client.check_crawl_status(crawl_id)
            result = {
                "status": getattr(status, "status", None),
                "total": getattr(status, "total", None),
                "completed": getattr(status, "completed", None),
                "success": getattr(status, "success", None),
                "next": getattr(status, "next", None),
                # Page payloads: the poll ingest path stores these. Dropping
                # them here previously made completed crawls save 0 pages.
                "data": _serialize_crawl_pages(getattr(status, "data", None)),
            }
            _cache_put(crawl_id, result)
            return result
        except Exception as e:
            err = str(e)
            # Firecrawl expires jobs after a retention window; polling an
            # expired crawl returns 404 "Job not found". Treat that as a
            # terminal failed state so the UI stops polling instead of
            # erroring forever.
            if "404" in err and "not found" in err.lower():
                logger.warning(
                    f"Firecrawl crawl job expired/not found for {crawl_id}: {err}"
                )
                result = {"status": "failed", "error": err}
                _cache_put(crawl_id, result)
                return result
            if "429" in err:
                # Don't fail polls on rate limits either; keep last known
                # state. First poll will wait for the reset on the next call.
                logger.warning(
                    f"Firecrawl rate limit on status poll for {crawl_id}; "
                    f"returning cached/unknown state"
                )
                result = {"status": "unknown", "error": err}
                _cache_put(crawl_id, result)
                return result
            logger.error(
                f"Firecrawl crawl status fetch failed for {crawl_id}: {e}",
                exc_info=True,
            )
            raise


_service_singleton: Optional[FirecrawlService] = None


def get_firecrawl_service() -> "FirecrawlService":
    global _service_singleton
    if _service_singleton is None:
        from app.core.config import settings
        _service_singleton = FirecrawlService(settings.FIRECRAWL_API_KEY)
    return _service_singleton
