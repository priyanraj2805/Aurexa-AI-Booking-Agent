"""
WhatsApp Cloud API webhook handler.

Receives inbound messages from Meta, routes them through the
channel-agnostic message_processor, and sends replies back.

Includes deduplication and sequential processing to handle Meta's
webhook retries and out-of-order message delivery.
"""

import asyncio
import hashlib
import hmac
import logging
from cachetools import TTLCache
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from starlette.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.db.database import get_db
from app.models.organization import Organization
from app.models.config import OrgConfig
from app.crud import chat as crud_chat
from app.engine import runtime_router
from app.core import whatsapp_client
from app.core.limiter import limiter
from app.core.whatsapp_debouncer import (
    is_duplicate,
    get_session_lock,
    start_debouncer,
    stop_debouncer,
)
from app.core.config import settings

router = APIRouter()
logger = logging.getLogger(__name__)

# Per-sender rate limiting cache: max 10 messages per sender per 60 seconds
_sender_rate_cache: TTLCache = TTLCache(maxsize=10000, ttl=60)


# ── Verification Hand─────────────────────────────────────────


# For development/testing: set a default verify token from environment
import os

DEFAULT_WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")


@router.get("/webhook")
async def verify_webhook(
    db: AsyncSession = Depends(get_db),
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Meta sends a GET request with hub.mode, hub.verify_token, hub.challenge.
    We look up the verify_token across all orgs and return hub.challenge.

    For development, also accepts DEFAULT_WHATSAPP_VERIFY_TOKEN.
    """
    if hub_mode != "subscribe" or not hub_verify_token:
        raise HTTPException(status_code=403, detail="Invalid verification request")

    # First, try to find a config with this verify token
    result = await db.execute(
        select(OrgConfig).where(
            OrgConfig.whatsapp_verify_token == hub_verify_token,
            OrgConfig.whatsapp_enabled == True,
        )
    )
    org_config = result.scalars().first()

    # If not found, check if it's the default token (for development only)
    if not org_config:
        if hub_verify_token == DEFAULT_WHATSAPP_VERIFY_TOKEN:
            logger.info("Using default verify token for webhook verification")
        else:
            logger.warning(
                f"Webhook verify token '{hub_verify_token[:10]}...' not found in any config"
            )
            raise HTTPException(
                status_code=403,
                detail="Invalid verify token. Configure WhatsApp in admin panel first.",
            )

    # Return the challenge (Meta expects this)
    if hub_challenge:
        try:
            return int(hub_challenge)
        except ValueError:
            return hub_challenge

    return "verified"


# ── Inbound Message Handler ──────────────────────────────────


@router.post("/webhook")
@limiter.limit("60/minute")
async def whatsapp_webhook(
    request: Request,
    data: dict,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """
    Receives inbound WhatsApp messages from Meta Cloud API.

    Flow:
    1. Parse Meta webhook payload → extract phone_number_id, from_phone, message_text
    2. Look up org by phone_number_id → load OrgConfig
    3. Get or create a ChatSession for this WhatsApp user
    4. Run through process_message() (same engine as web widget)
    5. Convert ActionPayload → WhatsApp message format
    6. Send reply via WhatsApp Cloud API
    """
    # HMAC-SHA256 signature verification
    app_secret = settings.WHATSAPP_APP_SECRET
    if app_secret:
        raw_body = await request.body()
        signature_header = request.headers.get("X-Hub-Signature-256", "")
        expected = (
            "sha256="
            + hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(expected, signature_header):
            return JSONResponse(
                status_code=403, content={"detail": "Invalid signature"}
            )
    else:
        logger.warning(
            "WHATSAPP_APP_SECRET not configured — skipping signature verification"
        )

    # Meta sometimes sends verification pings with no useful data
    if not data.get("entry"):
        return {"status": "ok"}

    try:
        entry = data["entry"][0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})

        # Only process "messages" events (ignore status updates, etc.)
        if "messages" not in value:
            return {"status": "ignored"}

        metadata = value.get("metadata", {})
        phone_number_id = metadata.get("phone_number_id")
        messages = value.get("messages", [])

        if not messages or not phone_number_id:
            return {"status": "ignored"}

        message = messages[0]
        from_phone = message.get("from")
        msg_type = message.get("type", "text")

        # Extract unique message ID for deduplication
        message_id = message.get("id")

        # Extract text from different message types
        if msg_type == "text":
            message_text = message.get("text", {}).get("body", "")
        elif msg_type == "interactive":
            interactive = message.get("interactive", {})
            int_type = interactive.get("type")
            if int_type == "button_reply":
                message_text = interactive.get("button_reply", {}).get("title", "")
            elif int_type == "list_reply":
                message_text = interactive.get("list_reply", {}).get("title", "")
            else:
                message_text = ""
        else:
            # Unsupported message type (image, audio, etc.)
            message_text = ""

        if not from_phone or not message_text:
            return {"status": "ignored"}

        # Per-sender rate limiting (max 10 messages per sender per 60 seconds)
        _count = _sender_rate_cache.get(from_phone, 0)
        if _count >= 10:
            logger.warning(f"Rate limit exceeded for sender: {from_phone[-4:]}")
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many messages. Please wait before sending again."
                },
            )
        _sender_rate_cache[from_phone] = _count + 1

        # Check for duplicate message (Meta sometimes retries webhooks)
        if message_id and is_duplicate(message_id):
            logger.info(f"Dropping duplicate WhatsApp message: {message_id}")
            return {"status": "duplicate"}

        logger.info(
            f"WhatsApp message from {from_phone[:4]}*** to phone_number_id {phone_number_id}"
        )

        # ── Look up organization by phone_number_id ───────────
        config_result = await db.execute(
            select(OrgConfig).where(
                OrgConfig.whatsapp_phone_number_id == phone_number_id,
                OrgConfig.whatsapp_enabled == True,
            )
        )
        org_config = config_result.scalars().first()
        if not org_config:
            logger.warning(
                f"No org found for WhatsApp phone_number_id: {phone_number_id}"
            )
            return {"status": "ignored"}

        org_result = await db.execute(
            select(Organization).where(Organization.id == org_config.org_id)
        )
        org = org_result.scalars().first()
        if not org or not org.is_active:
            return {"status": "ignored"}

        # ── Get or create session ─────────────────────────────
        logger.info(
            f"WhatsApp: Creating/finding session for org_id={org.id}, channel=whatsapp, user={from_phone}"
        )
        chat_session = await crud_chat.get_or_create_channel_session(
            db, str(org.id), "whatsapp", from_phone
        )
        logger.info(
            f"WhatsApp: Session created/found - session_id={chat_session.session_id}, channel={chat_session.channel}"
        )

        # ── Process message with session lock to ensure sequential handling ──
        # This prevents race conditions when Meta sends messages out of order
        session_lock = get_session_lock(str(chat_session.session_id))

        async def process_with_lock():
            # ── Send typing indicator to user ─────────────────────
            wa_token = org_config.whatsapp_api_token
            api_token = str(wa_token) if wa_token is not None else ""
            try:
                await whatsapp_client.send_typing_indicator(
                    phone_number_id, api_token, from_phone, is_typing=True
                )
            except Exception as typ_err:
                logger.warning(f"Failed to send typing indicator: {typ_err}")

            # ── Process through the engine ────────────────────────
            action = await runtime_router.route_whatsapp_message(
                db=db,
                org=org,
                org_config=org_config,
                chat_session=chat_session,
                message_text=message_text,
                background_tasks=background_tasks,
            )

            # ── Turn off typing indicator ─────────────────────────
            try:
                await whatsapp_client.send_typing_indicator(
                    phone_number_id, api_token, from_phone, is_typing=False
                )
            except Exception:
                pass

            return action, api_token, phone_number_id, from_phone

        # Acquire lock and process sequentially for this session
        async with session_lock:
            action, api_token, phone_number_id, from_phone = await process_with_lock()

            # ── Send reply via WhatsApp Cloud API ─────────────────
            wa_msg = whatsapp_client.format_action_for_whatsapp(action)

            try:
                if wa_msg["type"] == "interactive_buttons":
                    await whatsapp_client.send_interactive_buttons(
                        phone_number_id,
                        api_token,
                        from_phone,
                        wa_msg["body"],
                        wa_msg["buttons"],
                    )
                elif wa_msg["type"] == "interactive_list":
                    await whatsapp_client.send_interactive_list(
                        phone_number_id,
                        api_token,
                        from_phone,
                        wa_msg["body"],
                        wa_msg.get("button_text", "Choose"),
                        wa_msg["sections"],
                    )
                else:
                    await whatsapp_client.send_text_message(
                        phone_number_id,
                        api_token,
                        from_phone,
                        wa_msg["body"],
                    )
            except Exception as send_err:
                logger.error(f"Failed to send WhatsApp reply: {send_err}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"WhatsApp webhook processing failed: {e}", exc_info=True)
        return {"status": "error"}
