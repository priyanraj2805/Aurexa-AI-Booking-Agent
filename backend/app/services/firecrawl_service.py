"""Firecrawl v4 service wrapper.

Uses firecrawl-py v4's AsyncV1FirecrawlApp (the modern async API).
"""

import logging
from typing import Any, Dict, Optional

from firecrawl.v1 import AsyncV1FirecrawlApp
from firecrawl.v1.client import V1ScrapeOptions

logger = logging.getLogger(__name__)


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
        """Start an async crawl. Returns the crawl ID (string)."""
        try:
            scrape_options = V1ScrapeOptions(formats=["markdown"])
            kwargs: Dict[str, Any] = {
                "limit": limit,
                "scrape_options": scrape_options,
            }
            if webhook_url:
                kwargs["webhook"] = webhook_url

            response = await self.client.async_crawl_url(url, **kwargs)
            crawl_id = getattr(response, "id", None) or str(response)
            return str(crawl_id)
        except Exception as e:
            logger.error(f"Firecrawl crawl start failed for {url}: {e}", exc_info=True)
            raise

    async def get_crawl_status(self, crawl_id: str) -> Dict[str, Any]:
        """Return crawl status as a plain dict for the API response."""
        try:
            status = await self.client.check_crawl_status(crawl_id)
            return {
                "status": getattr(status, "status", None),
                "total": getattr(status, "total", None),
                "completed": getattr(status, "completed", None),
                "success": getattr(status, "success", None),
                "next": getattr(status, "next", None),
            }
        except Exception as e:
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
