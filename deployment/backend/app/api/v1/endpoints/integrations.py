"""
OAuth callback endpoints for external integrations (Google, etc.)
These routes don't require JWT auth since they're called by external OAuth providers.
"""

import logging
from fastapi import APIRouter, Depends, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.database import get_db
from app.models.config import OrgConfig, CalendarProviderType
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


def _integrations_redirect_url(**params: str) -> str:
    from urllib.parse import urlencode

    base_url = settings.FRONTEND_URL.rstrip("/")
    qs = urlencode(params)
    return f"{base_url}/dashboard/integrations?{qs}" if qs else f"{base_url}/dashboard/integrations"


@router.get("/google/callback")
async def google_oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """
    OAuth callback endpoint for Google Calendar.
    This is the legacy path kept for compatibility.

    After user authorizes, Google redirects here with:
    - code: authorization code to exchange for tokens
    - state: org_id to associate the token with
    """
    from app.core.integrations.google_calendar import exchange_code_for_tokens

    org_id = state

    result = await db.execute(select(OrgConfig).where(OrgConfig.org_id == org_id))
    config = result.scalars().first()

    if not config:
        logger.error(
            f"Google Calendar callback: OrgConfig not found for org_id={org_id}"
        )
        return RedirectResponse(
            url=_integrations_redirect_url(error="org_not_found"),
            status_code=303,
        )

    if not code:
        logger.error("Google Calendar callback: No authorization code received")
        return RedirectResponse(
            url=_integrations_redirect_url(error="no_code"),
            status_code=303,
        )

    tokens = await exchange_code_for_tokens(code)

    if not tokens or "refresh_token" not in tokens:
        logger.error(
            f"Google Calendar callback: Failed to get tokens. tokens_present={bool(tokens)} refresh_token_present={'refresh_token' in tokens if tokens else False}"
        )
        return RedirectResponse(
            url=_integrations_redirect_url(error="token_exchange_failed"),
            status_code=303,
        )

    config.google_calendar_refresh_token = tokens["refresh_token"]
    config.google_calendar_connected = True
    config.google_calendar_enabled = True
    if config.active_calendar_provider in (None, CalendarProviderType.NONE):
        config.active_calendar_provider = CalendarProviderType.GOOGLE
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to save Google Calendar config: {e}")
        return RedirectResponse(
            url=_integrations_redirect_url(error="save_failed"),
            status_code=303,
        )

    logger.info(f"Google Calendar connected for org_id={org_id}")
    return RedirectResponse(
        url=_integrations_redirect_url(google_connected="true"),
        status_code=303,
    )
