"""
WhatsApp department (unit) picker.

The web widget renders a department dropdown BEFORE the chat starts, so
`unit_id` is already pinned there. WhatsApp has no pre-chat UI — the
customer only exists once they send a message — so the department list
has to be offered INSIDE the conversation and the reply matched back.

Without this, WhatsApp customers never learn the business has departments
or team members, and the member picker (which requires a pinned unit)
never fires.

Deliberately NOT keyword-driven:
  * WHICH departments exist comes from the DATABASE, not a hardcoded list.
  * The customer's reply is matched against the real department names
    (exact / fragment / prefix / name-inside-sentence) — no word lists.
  * WHETHER to show the list is decided by the agent's own output (a
    collection turn that is not a knowledge answer) — never by matching
    "book"/"appointment" in the message. A hardcoded keyword list here
    would break on every new language and every new phrasing.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import chat as crud_chat
from app.schemas.agent import ActionPayload, ChatSessionState
from app.services.catalog_service import get_active_units, match_unit_choice

logger = logging.getLogger(__name__)

WHATSAPP_CHANNELS = ("whatsapp", "whatsapp_cloud")

# collected_data flag: the department list has been offered once for this
# session (the customer may still name a department at any later point).
DEPT_OFFERED_KEY = "_dept_offered"

# Soft cap on the list message. A business with 60+ departments would blow
# past WhatsApp's message limit, so the list is truncated by LENGTH — never
# by a fixed count — and the customer is told to type the name. The name is
# matched against the FULL set, so nothing becomes unreachable.
MAX_LIST_CHARS = 2200


def is_whatsapp_channel(channel: Any) -> bool:
    return str(channel or "").lower() in WHATSAPP_CHANNELS


def _units_line(unit: Dict[str, Any]) -> str:
    name = str(unit.get("name") or "").strip()
    count = int(unit.get("member_count") or 0)
    if count:
        plural = "member" if count == 1 else "members"
        return f"{name} ({count} {plural})"
    return name


def _render_unit_list(units: List[Dict[str, Any]]) -> Tuple[str, List[Dict[str, Any]]]:
    """Numbered department list, truncated by length (never by a fixed count)."""
    lines: List[str] = []
    shown: List[Dict[str, Any]] = []
    used = 0
    for i, u in enumerate(units, start=1):
        line = f"{i}. {_units_line(u)}"
        if used + len(line) + 1 > MAX_LIST_CHARS:
            break
        lines.append(line)
        shown.append(u)
        used += len(line) + 1
    return "\n".join(lines), shown


def _norm(value: Any) -> str:
    """Compare department names and captured values on letters/digits only."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _unit_pinned(chat_session: ChatSessionState) -> bool:
    collected = (
        chat_session.collected_data
        if isinstance(chat_session.collected_data, dict)
        else {}
    )
    if str(collected.get("unit_id") or "").strip():
        return True
    return bool(getattr(chat_session, "unit_id", None))


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


async def try_pick_department(
    db: AsyncSession,
    org,
    chat_session: ChatSessionState,
    message_text: str,
) -> Optional[Dict[str, Any]]:
    """Pin the department the customer named. Returns the unit, or None.

    The reply is matched against the real department names in the DB, so
    every department the business has is reachable — there is no list of
    "magic words" the customer has to know.

    A department named AFTER one is already pinned replaces it (the
    customer changed their mind); naming the same one again is a no-op.
    Either way the reply is a department, never a customer detail.

    The caller keeps the booking flow running: the graph still sees the
    turn (it must, to advance the flow). If the extractor filed the reply
    under the customer's details — a bare "Cardiology" reads exactly like a
    name — `detail_captured_from_department` flags it and the orchestrator
    drops that capture and asks the field again.
    """
    if not is_whatsapp_channel(getattr(chat_session, "channel", "")):
        return None
    try:
        picked = await match_unit_choice(db, org.id, message_text)
    except Exception:
        logger.warning("department match failed", exc_info=True)
        return None
    if picked is None:
        return None
    await _pin_unit(db, chat_session, picked)
    logger.info(
        "whatsapp department picked org=%s unit=%s",
        getattr(org, "id", "?"),
        str(picked.get("name")),
    )
    return picked


def detail_captured_from_department(
    captured_deltas: Any, unit: Any
) -> Optional[str]:
    """The field whose captured value is really the DEPARTMENT name.

    A bare "Cardiology" reads like a name to the extractor, so the graph may
    file the department reply under the customer's name. That capture is
    bogus — the customer named a department, not themselves. Compared
    against the real department name (data), never a word list.
    """
    if not isinstance(captured_deltas, dict) or not captured_deltas:
        return None
    dept_name = unit.get("name") if isinstance(unit, dict) else unit
    dept_norm = _norm(dept_name)
    if not dept_norm:
        return None
    for field, value in captured_deltas.items():
        if _norm(value) == dept_norm:
            return str(field)
    return None


async def build_department_offer(
    db: AsyncSession,
    org,
    chat_session: ChatSessionState,
) -> Optional[ActionPayload]:
    """Offer the department list as plain text.

    Every department the business actually has is listed (truncated only by
    message length). The customer replies with a number or the name.
    """
    if not is_whatsapp_channel(getattr(chat_session, "channel", "")):
        return None
    if _unit_pinned(chat_session):
        return None
    collected = dict(chat_session.collected_data or {})
    if collected.get(DEPT_OFFERED_KEY):
        return None  # already offered once for this session
    try:
        units = await get_active_units(db, org.id)
    except Exception:
        logger.warning("department list lookup failed", exc_info=True)
        return None
    if not units:
        return None  # individual business, no departments

    body, shown = _render_unit_list(units)
    if not body:
        return None

    collected[DEPT_OFFERED_KEY] = True
    chat_session.collected_data = collected
    try:
        await crud_chat.update_chat_session(
            db, chat_session.session_id, chat_session
        )
    except Exception:
        logger.warning("department offer persist failed", exc_info=True)

    tail = "Reply with the number or the department name."
    if len(shown) < len(units):
        tail = (
            "Reply with the number or the exact department name — "
            f"{len(units) - len(shown)} further department(s) are not shown, "
            "but typing the name always works."
        )
    msg = (
        "Which department would you like to visit?\n"
        + body
        + f"\n\n{tail}"
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
                for u in shown
            ],
        },
    )


async def team_members_for_date(
    db: AsyncSession,
    org,
    unit_id: Any,
    target_date: Any = None,
) -> List[Dict[str, Any]]:
    """Members of a department who work on a given date.

    Data-driven: the list comes from the schedule (each member's own
    override, else the org template, minus CLOSED exceptions), never from
    a hardcoded roster.
    """
    if not unit_id or not target_date:
        return []
    try:
        from app.services.catalog_service import providers_for_date

        return await providers_for_date(db, org.id, unit_id, target_date)
    except Exception:
        logger.warning("team members for date failed", exc_info=True)
        return []


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
