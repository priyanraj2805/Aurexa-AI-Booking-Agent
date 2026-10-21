"""
WhatsApp department (unit) picker.

The web widget renders a department dropdown BEFORE the chat starts, so
`unit_id` is already pinned when the booking flow runs. WhatsApp has no
pre-chat UI — the customer only exists once they send a message — so the
department list has to be offered INSIDE the conversation and the reply
matched back.

Without this, WhatsApp customers never learn the business has departments
or team members, and the member picker (which requires a pinned unit)
never fires.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import chat as crud_chat
from app.schemas.agent import ActionPayload, ChatSessionState
from app.services.catalog_service import get_active_units, match_unit_choice

logger = logging.getLogger(__name__)

WHATSAPP_CHANNELS = ("whatsapp", "whatsapp_cloud")

# Cap the numbered list — a big business has 20+ departments and a wall of
# text is unreadable. The customer can always type the name instead.
MAX_LISTED_UNITS = 12

# collected_data flag: the department list has been offered and we are
# waiting for the customer's pick.
DEPT_OFFERED_KEY = "_dept_offered"


def is_whatsapp_channel(channel: Any) -> bool:
    return str(channel or "").lower() in WHATSAPP_CHANNELS


def _units_line(unit: Dict[str, Any]) -> str:
    name = str(unit.get("name") or "").strip()
    count = int(unit.get("member_count") or 0)
    if count:
        plural = "member" if count == 1 else "members"
        return f"{name} ({count} {plural})"
    return name


def _booking_fields_already_collected(collected: Dict[str, Any]) -> bool:
    """True when the customer already gave us booking details.

    The department question must not interrupt an in-progress collection
    (name/email/phone) — only the start of a booking or a reply to our
    own offer may trigger it.
    """
    for key in ("name", "email", "phone"):
        if str(collected.get(key) or "").strip():
            return True
    return False


async def _pin_unit(
    db: AsyncSession, chat_session: ChatSessionState, unit: Dict[str, Any]
) -> None:
    """Pin the chosen department onto the session.

    `collected_data` is the source of truth: the engine reads the unit from
    there (`collected_data.get("unit_id")`), and `update_chat_session`
    persists it into `chat_sessions.state_data`. `ChatSessionState` has no
    `unit_id` field, so setting an attribute on it would raise.
    """
    collected = dict(chat_session.collected_data or {})
    collected["unit_id"] = str(unit["id"])
    collected["unit_name"] = str(unit.get("name") or "")
    chat_session.collected_data = collected
    try:
        await crud_chat.update_chat_session(
            db,
            chat_session.session_id,
            chat_session,
            remove_keys=(DEPT_OFFERED_KEY,),
        )
    except Exception:
        logger.warning("pin unit failed", exc_info=True)


async def maybe_handle_department_pick(
    db: AsyncSession,
    org,
    chat_session: ChatSessionState,
    message_text: str,
) -> Optional[ActionPayload]:
    """Offer / resolve the department choice on WhatsApp.

    Returns an ActionPayload when the department question must be sent
    (offer made). Returns None when the flow should CONTINUE — either the
    customer picked a department (pinned) or this org has no catalog.
    """
    if not is_whatsapp_channel(getattr(chat_session, "channel", "")):
        return None
    if getattr(chat_session, "unit_id", None):
        return None  # already pinned
    collected = chat_session.collected_data if isinstance(chat_session.collected_data, dict) else {}
    if str(collected.get("unit_id") or "").strip():
        return None  # already pinned via collected_data

    try:
        units = await get_active_units(db, org.id)
    except Exception:
        logger.warning("department list lookup failed", exc_info=True)
        return None
    if not units:
        return None  # individual business, no departments

    offered_before = bool(collected.get(DEPT_OFFERED_KEY))
    if not offered_before and _booking_fields_already_collected(collected):
        # Mid-collection — never interrupt. The member picker still works
        # once a unit is pinned some other way.
        return None

    # Is this message the customer's pick?
    picked = await match_unit_choice(db, org.id, message_text)
    if picked is not None:
        await _pin_unit(db, chat_session, picked)
        logger.info(
            "whatsapp department picked org=%s unit=%s",
            getattr(org, "id", "?"),
            str(picked.get("name")),
        )
        return None  # continue the normal booking flow

    # Not a pick (yet) — offer the list. Capped: a 20-department business
    # would otherwise dump a wall of text, and the customer can always
    # type the name instead of the number.
    lines = []
    for i, u in enumerate(units[:MAX_LISTED_UNITS], start=1):
        lines.append(f"{i}. {_units_line(u)}")
    if len(units) > MAX_LISTED_UNITS:
        lines.append(f"(+{len(units) - MAX_LISTED_UNITS} more — reply with the name)")
    new_collected = dict(collected)
    new_collected[DEPT_OFFERED_KEY] = True
    chat_session.collected_data = new_collected
    try:
        await crud_chat.update_chat_session(
            db, chat_session.session_id, chat_session
        )
    except Exception:
        logger.warning("department offer persist failed", exc_info=True)

    msg = (
        "Which department would you like to visit?\n"
        + "\n".join(lines)
        + "\n\nReply with the number or the department name."
    )
    return ActionPayload(
        message=msg,
        widget_directive="TEXT_INPUT",
        field_meta={
            "name": "unit",
            "type": "unit",
            "label": "Choose department",
            "units": [
                {"id": u["id"], "name": u["name"], "member_count": u.get("member_count")}
                for u in units
            ],
        },
    )


async def offer_team_members(
    db: AsyncSession,
    org,
    unit_id: Any,
    target_date: Any = None,
) -> Tuple[str, list]:
    """Plain-text list of team members for a department.

    Used when the customer has a department but the engine could not
    produce the member picker (no date yet, or date has no availability).
    Returns (text, members).
    """
    try:
        from app.services.catalog_service import providers_for_date
        from datetime import datetime as _dt

        day = None
        if target_date:
            try:
                from dateutil.parser import parse as _parse

                day = _parse(str(target_date)).date()
            except Exception:
                day = None
        members = await providers_for_date(db, org.id, unit_id, day) if day else []
        if not members and day is None:
            # No date → list every active member of the unit.
            from sqlalchemy.future import select as _sel
            from app.models.service_unit import Provider, UnitProvider

            rows = (
                await db.execute(
                    _sel(Provider)
                    .join(UnitProvider, UnitProvider.provider_id == Provider.id)
                    .where(
                        UnitProvider.unit_id == unit_id,
                        Provider.org_id == org.id,
                        Provider.is_active.is_(True),
                    )
                )
            ).scalars().all()
            members = [
                {
                    "id": str(p.id),
                    "full_name": p.full_name,
                    "role_label": getattr(p, "role_label", None),
                }
                for p in rows
            ]
        if not members:
            return ("", [])
        lines = []
        for i, m in enumerate(members, start=1):
            label = str(m.get("full_name") or "").strip()
            if m.get("role_label"):
                label += f" · {m['role_label']}"
            if m.get("fee_display"):
                label += f" · {m['fee_display']}"
            lines.append(f"{i}. {label}")
        return ("\n".join(lines), members)
    except Exception:
        logger.warning("team member list failed", exc_info=True)
        return ("", [])
