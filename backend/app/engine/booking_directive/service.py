"""BookingDirectiveService — pure booking logic, no WhatsApp.

Wraps StateEvaluator + slot_engine for real availability, handles
PENDING/COMPLETED/EXPIRED, idempotent via status, stale-slot re-verify.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_
from sqlalchemy.future import select

from app.models.booking_directive import BookingDirective, DirectiveStatus, DirectiveType


async def create_directive(
    db: AsyncSession,
    *,
    org_id,
    customer_id: str,
    session_id,
    type: str,
    title: str,
    description: str = "",
    options: List[dict],
    required: bool = True,
    expires_in_seconds: int = 300,
    meta: Optional[dict] = None,
    booking_id=None,
) -> BookingDirective:
    now = datetime.now(timezone.utc)
    old_rows = (
        await db.execute(
            select(BookingDirective).where(
                BookingDirective.session_id == session_id,
                BookingDirective.status == DirectiveStatus.PENDING,
            )
        )
    ).scalars().all()
    for old in old_rows:
        old.status = DirectiveStatus.EXPIRED
    if old_rows:
        await db.flush()
    expires_at = now + timedelta(seconds=expires_in_seconds) if expires_in_seconds else None
    row = BookingDirective(
        id=uuid.uuid4(),
        booking_id=booking_id,
        org_id=org_id,
        customer_id=str(customer_id),
        session_id=session_id,
        type=type,
        status=DirectiveStatus.PENDING,
        title=title,
        description=description,
        options=options,
        required=required,
        expires_at=expires_at,
        meta=meta or {},
    )
    db.add(row)
    await db.flush()
    return row


async def _clear_session_pointer(db: AsyncSession, directive: BookingDirective) -> None:
    from app.models.session import ChatSession
    from sqlalchemy.orm.attributes import flag_modified

    result = await db.execute(
        select(ChatSession).where(ChatSession.id == directive.session_id)
    )
    session = result.scalars().first()
    if session is None:
        return
    state = dict(session.state_data) if isinstance(session.state_data, dict) else {}
    if str(state.get("active_directive_id") or "") == str(directive.id):
        state.pop("active_directive_id", None)
        session.state_data = state
        flag_modified(session, "state_data")


_STEP_ACTION_DIRECTIVES = {
    "SELECT_DATE": {"REQUIRE_DATE_PICKER"},
    "SELECT_SLOT": {"REQUIRE_TIME_SLOT_GRID", "REQUIRE_SELECT"},
    "SELECT_TIME": {"REQUIRE_TIME_SLOT_GRID", "REQUIRE_SELECT"},
    "CONFIRM_BOOKING": {"REQUEST_CONFIRMATION", "PROCESS_BOOKING", "BOOKING_CONFIRMED_CARD"},
}


def directive_frame_matches_action(directive_type, action_directive) -> bool:
    """True only when the pending step's frame matches what the engine just produced.

    The WhatsApp transports wrap the engine reply in the pending directive's
    title/options. Without this gate, a stale step (e.g. CONFIRM_BOOKING)
    frames unrelated replies — edit prompts, delete lookups — with
    "Please confirm your booking … Reply CONFIRM", misleading the customer
    into typing CONFIRM in the wrong place.
    """
    allowed = _STEP_ACTION_DIRECTIVES.get(str(directive_type or ""))
    if not allowed:
        return False
    return str(action_directive or "") in allowed


async def get_active(db: AsyncSession, session_id, type: Optional[str] = None):
    now = datetime.now(timezone.utc)
    stmt = select(BookingDirective).where(
        BookingDirective.session_id == session_id,
        BookingDirective.status == DirectiveStatus.PENDING,
        or_(
            BookingDirective.expires_at.is_(None),
            BookingDirective.expires_at > now,
        ),
    )
    if type:
        stmt = stmt.where(BookingDirective.type == type)
    stmt = stmt.order_by(BookingDirective.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().first()


async def get_by_id(db: AsyncSession, directive_id) -> Optional[BookingDirective]:
    res = await db.execute(select(BookingDirective).where(BookingDirective.id == directive_id))
    return res.scalars().first()


async def verify(db: AsyncSession, directive_id, customer_id: str, option_id: str):
    """Verify directive exists, belongs to user, not expired/completed, option valid.
    Returns (ok:bool, reason:str, directive).
    """
    row = await get_by_id(db, directive_id)
    if not row:
        return False, "not_found", None
    if str(row.customer_id) != str(customer_id):
        return False, "wrong_user", row
    if row.status != DirectiveStatus.PENDING:
        # Idempotent: if already COMPLETED with same option, treat as ok (no re-book)
        if row.status == DirectiveStatus.COMPLETED and row.selected_option and row.selected_option.get("id") == option_id:
            return True, "already_completed_same_option", row
        return False, f"not_pending_{row.status.lower()}", row
    if row.expires_at and row.expires_at < datetime.now(timezone.utc):
        row.status = DirectiveStatus.EXPIRED
        await _clear_session_pointer(db, row)
        await db.flush()
        return False, "expired", row
    ids = {str(o.get("id")) for o in (row.options or [])}
    if str(option_id) not in ids:
        return False, "invalid_option", row
    return True, "ok", row


async def complete(db: AsyncSession, directive_id, option_id, booking_id=None) -> BookingDirective:
    row = await get_by_id(db, directive_id)
    if not row:
        raise ValueError("directive not found")
    if row.status != DirectiveStatus.PENDING:
        return row
    # Find selected option
    sel = next((o for o in (row.options or []) if str(o.get("id")) == str(option_id)), None)
    row.selected_option = sel or {"id": str(option_id)}
    row.status = DirectiveStatus.COMPLETED
    row.completed_at = datetime.now(timezone.utc)
    if booking_id:
        row.booking_id = booking_id
    await _clear_session_pointer(db, row)
    await db.flush()
    return row


async def expire_stale(db: AsyncSession) -> int:
    now = datetime.now(timezone.utc)
    res = await db.execute(
        select(BookingDirective).where(
            BookingDirective.status == DirectiveStatus.PENDING,
            BookingDirective.expires_at != None,  # noqa: E711
            BookingDirective.expires_at < now,
        )
    )
    rows = res.scalars().all()
    for r in rows:
        r.status = DirectiveStatus.EXPIRED
    if rows:
        await db.flush()
    return len(rows)


async def expire_pending_by_type(db: AsyncSession, org_id, types) -> int:
    """Expire PENDING directives of given types (admin changed the rules).

    Used when business hours / horizon change: stored date/time prompts and
    options go stale instantly instead of lingering until natural expiry.
    Returns the expired count. Never raises (best-effort).
    """
    try:
        rows = (
            await db.execute(
                select(BookingDirective).where(
                    BookingDirective.org_id == org_id,
                    BookingDirective.type.in_(list(types)),
                    BookingDirective.status == DirectiveStatus.PENDING,
                )
            )
        ).scalars().all()
        for r in rows:
            r.status = DirectiveStatus.EXPIRED
        if rows:
            await db.flush()
        return len(rows)
    except Exception:
        return 0


async def generate_for_next_step(db, org_id, session_id, customer_id, missing_fields, field_definitions, target_date=None):
    """Generate next BookingDirective from StateEvaluator + slot_engine.
    Caller decides when to call (after each turn). Returns directive or None if no missing.
    """
    from app.engine.evaluator import StateEvaluator

    if not missing_fields:
        return None
    # Use first missing as primary (StateEvaluator does same)
    next_action = StateEvaluator.determine_next_action(missing_fields, "START_BOOKING", field_definitions)
    directive_type_map = {
        "REQUIRE_DATE_PICKER": DirectiveType.SELECT_DATE,
        "REQUIRE_TIME_SLOT_GRID": DirectiveType.SELECT_SLOT,
        "REQUIRE_SELECT": DirectiveType.SELECT_SLOT,
        "SELECT_DROPDOWN": DirectiveType.SELECT_SLOT,
    }
    directive = next_action.get("directive", "TEXT_INPUT")
    dtype = directive_type_map.get(directive)
    if not dtype:
        # For CONFIRM etc., map separately
        if directive in ("REQUEST_CONFIRMATION", "BOOKING_SUMMARY_CARD"):
            dtype = DirectiveType.CONFIRM_BOOKING
        else:
            return None
    meta = next_action.get("field_meta") or {}
    title_map = {
        DirectiveType.SELECT_DATE: "Select your preferred date",
        DirectiveType.SELECT_SLOT: "Select your preferred time",
        DirectiveType.SELECT_TIME: "Select your preferred time",
        DirectiveType.CONFIRM_BOOKING: "Please confirm your booking",
    }
    title = title_map.get(dtype, "Please make a selection")
    # Build options from field_meta.options — but for SELECT_DATE/SLOT, if empty, generate authoritatively
    # so the directive is never skipped due to missing field_meta (the old renderer fallback).
    raw_opts = meta.get("options") or []
    if dtype == DirectiveType.SELECT_DATE and not raw_opts:
        try:
            from app.engine.message_processor import _format_available_dates_for_prompt, _upcoming_date_options

            # Prefer org timezone + horizon + business hours from DB if we have org_id, else fallback
            _tz = "UTC"
            _max = 30
            _slot_mins = None
            _business_hours_summary = ""
            _cfg2 = None
            try:
                from sqlalchemy.future import select as _sel2
                from app.models.config import OrgConfig as _OC2
                from app.models.booking import BusinessSchedule as _BS, ScheduleDay as _SD
                from app.engine.scheduling_policy import format_weekly_hours_summary as _fmt_hours

                _cfg2_res = await db.execute(_sel2(_OC2).where(_OC2.org_id == org_id))
                _cfg2 = _cfg2_res.scalars().first()
                if _cfg2:
                    _tz = str(getattr(_cfg2, "timezone", None) or "UTC")
                    from app.engine.scheduling_policy import resolve_booking_horizon_days as _resolve_horizon

                    _max = (await _resolve_horizon(db, org_id, _cfg2))[0]
                # try business hours summary (clean WhatsApp style, no horizon)
                try:
                    from app.engine.message_processor import _format_business_hours_for_whatsapp as _fmt_bh
                    _sched_res = await db.execute(_sel2(_BS).where(_BS.org_id == org_id))
                    _sched = _sched_res.scalars().first()
                    if _sched:
                        _slot_mins = getattr(_sched, "slot_duration_minutes", None)
                        _sd_res = await db.execute(
                            _sel2(_SD).where(
                                _SD.org_id == org_id,
                                _SD.provider_id.is_(None),
                            )
                        )
                        _sched_days = _sd_res.scalars().all()
                        _weekly = [
                            {"day_of_week": r.day_of_week, "is_active": r.is_active, "open_time": r.open_time, "close_time": r.close_time}
                            for r in _sched_days
                        ]
                        _business_hours_summary = _fmt_bh(_weekly, _tz, slot_minutes=_slot_mins)
                        # fallback to old if helper empty
                        if not _business_hours_summary:
                            _business_hours_summary = _fmt_hours(_weekly, _tz, slot_minutes=_slot_mins, booking_window_days=None)
                except Exception:
                    pass
            except Exception:
                pass
            compute_slot_inventory = None
            try:
                from zoneinfo import ZoneInfo
                from app.engine.slot_engine import compute_slot_inventory

                _zone = ZoneInfo(_tz)
            except Exception:
                from datetime import timezone as _tz_fallback

                _zone = _tz_fallback.utc
            _today = datetime.now(_zone).date()
            _allow_same_day = bool(getattr(_cfg2, "allow_same_day_booking", False))
            _gen_labels = []
            for _offset in range(int(_max or 30) + 1):
                if compute_slot_inventory is None:
                    break
                if _offset == 0 and not _allow_same_day:
                    continue
                _day = _today + timedelta(days=_offset)
                try:
                    _inventory, _status = await compute_slot_inventory(
                        db,
                        uuid.UUID(str(org_id)),
                        _day,
                        org_config=_cfg2,
                    )
                except Exception:
                    continue
                if _status == "ok" and any(
                    bool(item.get("available")) for item in _inventory
                ):
                    _gen_labels.append(_day.strftime("%a, %b %d"))
                if len(_gen_labels) >= 7:
                    break
            if _gen_labels:
                raw_opts = _gen_labels
                meta["options"] = list(raw_opts)
                # Date prompt: horizon only (as requested: "We are available from X to Y...")
                if not meta.get("description"):
                    try:
                        _avail = _format_available_dates_for_prompt(_tz, _max, count=min(7, len(_gen_labels)))
                        meta["description"] = _avail
                    except Exception:
                        pass
        except Exception:
            raw_opts = []
    if dtype == DirectiveType.SELECT_SLOT and not raw_opts:
        # Generate real available slots for the target date using the existing slot engine
        _cfg = None
        _tz_name = "UTC"
        try:
            from app.engine.slot_engine import compute_slot_inventory, format_slot_time
            from app.engine.scheduling_policy import resolve_effective_timezone_policy

            # target_date may be in meta or passed explicitly
            _target = target_date or meta.get("target_date") or meta.get("date")
            if not _target:
                # Try to infer from session's collected_data if available via meta
                _target = meta.get("collected_date") or meta.get("date")
            if _target:
                from dateutil.parser import parse as _parse

                _parsed_date = _parse(str(_target)).date()
                # Need org_id to compute slots — caller should provide org_id
                try:
                    from sqlalchemy.future import select as _sel
                    from app.models.config import OrgConfig as _OC

                    _cfg_res = await db.execute(_sel(_OC).where(_OC.org_id == org_id))
                    _cfg = _cfg_res.scalars().first()
                    if _cfg:
                        _tz_name = str(getattr(_cfg, "timezone", None) or "UTC")
                except Exception:
                    pass
                _inventory, _status = await compute_slot_inventory(db, org_id, _parsed_date, org_config=_cfg)
                # Apply scheduling policy to filter
                try:
                    from app.engine.scheduling_policy import resolve_effective_timezone_policy as _resolve_tz
                    from app.engine.slot_engine import apply_scheduling_policy_to_inventory

                    _tz_res = _resolve_tz(requested_tz=None, org_tz=_tz_name, requested_source=None)
                    _eff_tz = str(_tz_res.get("effective_timezone") or _tz_name)
                    _inventory, _status, _ = apply_scheduling_policy_to_inventory(
                        _inventory, _parsed_date, base_status=_status, effective_timezone=_eff_tz, allow_same_day_booking=False, slot_cutoff_minutes=5
                    )
                except Exception:
                    pass
                # Build raw_opts as time labels
                _tz_for_slots = _tz_name
                try:
                    from zoneinfo import ZoneInfo
                    _tz_obj = ZoneInfo(_tz_for_slots)
                except Exception:
                    import datetime as _dt

                    _tz_obj = _dt.timezone.utc
                _slot_labels = []
                for _item in _inventory:
                    if not _item.get("available"):
                        continue
                    _start = _item.get("start_utc")
                    if _start is None:
                        continue
                    try:
                        if getattr(_start, "tzinfo", None) is None:
                            import datetime as _dt2

                            _start = _start.replace(tzinfo=_dt2.timezone.utc)
                        _local = _start.astimezone(_tz_obj).time()
                        _slot_labels.append(format_slot_time(_local))
                    except Exception:
                        try:
                            _slot_labels.append(format_slot_time(_item["start_utc"].time()))
                        except Exception:
                            continue
                    if len(_slot_labels) >= 10:
                        break
                if _slot_labels:
                    raw_opts = _slot_labels
                    meta["options"] = list(raw_opts)
                    # Also set target_date in meta for stable IDs
                    if not meta.get("target_date") and _target:
                        meta["target_date"] = str(_parsed_date.isoformat())
                    # Add helpful description with hours + duration in requested format
                    if not meta.get("description"):
                        try:
                            from app.engine.message_processor import _format_time_prompt_for_whatsapp
                            # need weekly schedule + exceptions for proper hours/closure handling
                            _weekly2 = None
                            _excs2 = None
                            try:
                                from app.models.booking import BusinessSchedule as _BS3a, ScheduleDay as _SD3a, ScheduleException as _SE3a
                                from sqlalchemy.future import select as _sel3a
                                _sd3_res = await db.execute(
                                    _sel3a(_SD3a).where(
                                        _SD3a.org_id == org_id,
                                        _SD3a.provider_id.is_(None),
                                    )
                                )
                                _sd3 = _sd3_res.scalars().all()
                                _weekly2 = [
                                    {"day_of_week": r.day_of_week, "is_active": r.is_active, "open_time": r.open_time, "close_time": r.close_time}
                                    for r in _sd3
                                ]
                                _se3_res = await db.execute(
                                    _sel3a(_SE3a).where(
                                        _SE3a.org_id == org_id,
                                        _SE3a.provider_id.is_(None),
                                    )
                                )
                                _se3 = _se3_res.scalars().all()
                                _excs2 = [
                                    {"exception_date": r.exception_date, "exception_type": str(r.exception_type), "custom_open": r.custom_open, "custom_close": r.custom_close}
                                    for r in _se3
                                ]
                            except Exception:
                                pass
                            _slot_mins2 = None
                            try:
                                from sqlalchemy.future import select as _sel_slot
                                from app.models.booking import BusinessSchedule as _BS2
                                _s2_res = await db.execute(_sel_slot(_BS2).where(_BS2.org_id == org_id))
                                _s2 = _s2_res.scalars().first()
                                if _s2:
                                    _slot_mins2 = getattr(_s2, "slot_duration_minutes", None)
                            except Exception:
                                pass
                            meta["description"] = _format_time_prompt_for_whatsapp(
                                str(_parsed_date.isoformat()), _tz_name, _slot_mins2, weekly_schedule=_weekly2, exceptions=_excs2
                            )
                        except Exception:
                            # fallback old
                            try:
                                _tz_label = _tz_name
                                _slots_str = " · ".join(raw_opts[:7])
                                _pretty_date = _parsed_date.strftime("%a, %b %d")
                                meta["description"] = f"Our available booking hours are from {_slots_str}, please choose your time for booking."
                            except Exception:
                                pass
                        except Exception:
                            pass
        except Exception as _e:
            import logging

            logging.getLogger(__name__).warning(f"SELECT_SLOT option generation failed: {_e}", exc_info=True)
            raw_opts = []
    options = []
    for i, opt in enumerate(raw_opts[:10]):
        label = str(opt) if isinstance(opt, str) else str(opt.get("label") or opt.get("title") or opt)
        # For SELECT_DATE, value must be ISO (2026-09-17) while label stays human-readable (17 September)
        # raw_opts for dates are like "Mon, Sep 14" — parse to ISO via dateutil
        value = label
        oid = f"{dtype.lower()}_{i}_{label.replace(' ', '_')[:20]}"
        if dtype == DirectiveType.SELECT_DATE:
            try:
                from dateutil.parser import parse as _parse_date

                # _upcoming_date_options gives "Mon, Sep 14" without year — assume current year
                # Try to parse with current year context
                _parsed = _parse_date(label)
                # If year is default 1900 or far past, use target_date year or current year
                if _parsed.year < 2000:
                    from datetime import datetime as _dt

                    _now_year = _dt.now().year
                    # Try with explicit year
                    try:
                        _parsed = _parse_date(f"{label} {_now_year}")
                    except Exception:
                        pass
                value = _parsed.date().isoformat()
                oid = f"date_{value.replace('-', '')}"
            except Exception:
                value = label
                oid = f"date_{label.replace(' ', '_').replace(',', '')[:20]}"
        elif dtype == DirectiveType.SELECT_SLOT:
            oid = f"slot_{str(target_date or meta.get('name') or dtype).replace('-','')}_{label.replace(':','').replace(' ','')}"
        options.append({"id": oid, "label": label, "value": value})
    if dtype == DirectiveType.CONFIRM_BOOKING:
        options = [
            {"id": "confirm_booking", "label": "CONFIRM", "value": "confirm"},
            {"id": "change_booking", "label": "CHANGE", "value": "change"},
        ]
    # Expire: slot directives 5min (matches TimeSlot.locked_until), date 10min, confirm 10min
    expires = 300 if dtype in (DirectiveType.SELECT_SLOT, DirectiveType.SELECT_TIME) else 600
    return await create_directive(
        db,
        org_id=org_id,
        customer_id=customer_id,
        session_id=session_id,
        type=dtype,
        title=title,
        description=meta.get("description") or "",
        options=options,
        required=True,
        expires_in_seconds=expires,
        meta={"field_name": meta.get("name"), "directive": directive, **meta},
    )
