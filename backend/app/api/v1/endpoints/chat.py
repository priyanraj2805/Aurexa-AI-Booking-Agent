"""
Web Widget chat endpoint — thin HTTP adapter over the channel-agnostic
message_processor engine.
"""

import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.db.database import get_db
from app.crud.organization import validate_api_key
from app.models.organization import Organization
from app.models.config import OrgConfig
from app.schemas.chat import ChatMessageRequest, ChatMessageResponse
from app.crud import chat as crud_chat
from app.engine import runtime_router
from app.engine.user_visible_text import extract_user_visible_text
from app.core.limiter import limiter

router = APIRouter()
logger = logging.getLogger(__name__)


async def _get_org_from_api_key(api_key: str, db: AsyncSession) -> Organization:
    org = await validate_api_key(db, api_key)
    if not org or not org.is_active:
        raise HTTPException(status_code=403, detail="Invalid or inactive API Key")
    return org


async def _get_org_from_widget_token(token: str, db: AsyncSession) -> Organization:
    result = await db.execute(select(OrgConfig).where(OrgConfig.widget_token == token))
    config = result.scalars().first()
    if not config:
        raise HTTPException(status_code=403, detail="Invalid widget token")
    result = await db.execute(
        select(Organization).where(Organization.id == config.org_id)
    )
    org = result.scalars().first()
    if not org or not org.is_active:
        raise HTTPException(status_code=403, detail="Invalid or inactive organization")
    return org


async def get_current_org(
    x_org_api_key: str = Header(default=None, alias="X-Org-API-Key"),
    x_widget_token: str = Header(default=None, alias="X-Widget-Token"),
    db: AsyncSession = Depends(get_db),
) -> Organization:
    """
    Authenticate via EITHER X-Org-API-Key header (legacy) OR X-Widget-Token header (recommended).
    X-Org-API-Key gives full org access.
    X-Widget-Token only allows chat dispatch — no admin operations.
    """
    if not x_org_api_key and not x_widget_token:
        raise HTTPException(
            status_code=401,
            detail="Missing authentication. Provide X-Org-API-Key or X-Widget-Token header.",
        )

    if x_org_api_key:
        return await _get_org_from_api_key(x_org_api_key, db)
    return await _get_org_from_widget_token(x_widget_token, db)


@router.post("/message/dispatch", response_model=ChatMessageResponse)
@limiter.limit("30/minute")
async def dispatch_message(
    request: Request,
    body: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    org: Organization = Depends(get_current_org),
    db: AsyncSession = Depends(get_db),
):
    """
    Web-widget entry point. Manages HTTP session IDs, then delegates
    all business logic to the channel-agnostic process_message() engine.

    Accepts X-Org-API-Key (legacy) or X-Widget-Token (recommended) headers.
    """
    # Get or create session
    if body.session_id:
        chat_session = await crud_chat.get_chat_session(db, str(body.session_id))
        if not chat_session:
            raise HTTPException(status_code=404, detail="Session not found")
        if chat_session.org_id != str(org.id):
            raise HTTPException(status_code=403, detail="Forbidden")
    else:
        new_session = await crud_chat.create_chat_session(
            db, str(org.id), channel="web", mode=body.mode
        )
        chat_session = await crud_chat.get_chat_session(db, str(new_session.id))
        body.session_id = str(new_session.id)

    # Org-mode: validate + pin department/doctor to this chat session.
    # Individual sessions send nothing and keep old behavior.
    if body.unit_id or body.provider_id:
        try:
            from app.services.catalog_service import validate_catalog_selection

            _unit, _provider = await validate_catalog_selection(
                db, org.id, body.unit_id, body.provider_id
            )
            try:
                if _unit is not None:
                    chat_session.unit_id = _unit.id
                if _provider is not None:
                    chat_session.provider_id = _provider.id
            except Exception:
                pass
            try:
                cd = chat_session.collected_data or {}
                if _unit is not None:
                    cd["unit_id"] = str(_unit.id)
                    cd["unit_name"] = _unit.name
                if _provider is not None:
                    cd["provider_id"] = str(_provider.id)
                    cd["provider_name"] = _provider.full_name
                    # Explicit choice consumes any pending offered list.
                    cd.pop("_provider_options", None)
                chat_session.collected_data = cd
            except Exception:
                pass
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Widget conversation mode (ask | booking): pinned to collected_data so
    # every turn of the session honors it. Invalid values rejected by schema.
    if body.mode in ("ask", "booking"):
        try:
            cd = chat_session.collected_data or {}
            if cd.get("_mode") != body.mode:
                cd["_mode"] = body.mode
                chat_session.collected_data = cd
                await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)
        except Exception:
            pass

    # Thread additive timezone/slot metadata into session state for confirm-time persistence
    session_changed = False
    if body.selected_slot_id:
        chat_session.collected_data["selected_slot_id"] = str(body.selected_slot_id)
        session_changed = True
    if body.user_timezone:
        chat_session.collected_data["user_timezone"] = body.user_timezone
        session_changed = True
    if body.timezone_source:
        chat_session.collected_data["timezone_source"] = body.timezone_source
        session_changed = True
    if body.timezone_fallback_used is not None:
        chat_session.collected_data["timezone_fallback_used"] = body.timezone_fallback_used
        session_changed = True
    if body.timezone_fallback_reason:
        chat_session.collected_data["timezone_fallback_reason"] = body.timezone_fallback_reason
        session_changed = True
    if session_changed:
        await crud_chat.update_chat_session(db, chat_session.session_id, chat_session)

    # Get org config
    config_result = await db.execute(
        select(OrgConfig).where(OrgConfig.org_id == org.id)
    )
    org_config = config_result.scalars().first()
    if not org_config:
        raise HTTPException(
            status_code=500, detail="Organization configuration not found"
        )

    result = await runtime_router.route_web_message(
        db=db,
        org=org,
        org_config=org_config,
        chat_session=chat_session,
        message_text=body.message,
        background_tasks=background_tasks,
    )

    action_payload = result.model_dump()
    raw_message = action_payload.get("message", "") or ""
    action_payload["message"] = (
        extract_user_visible_text(raw_message)[0] if raw_message.strip() else ""
    )

    return ChatMessageResponse(
        session_id=body.session_id,
        action=action_payload,
    )
