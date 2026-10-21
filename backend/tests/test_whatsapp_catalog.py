"""WhatsApp department picker — the regression guard for the missing
department/team-member offer on WhatsApp.

The web widget renders a department dropdown BEFORE the chat starts, so
`unit_id` is already pinned there. WhatsApp has no pre-chat UI, so the
list has to be offered in-conversation. Without this, WhatsApp customers
never learned the business had departments or team members.
"""
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import whatsapp_catalog as wc
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.service_unit import Provider, ServiceUnit, UnitProvider
from app.models.session import ChatSession
from app.schemas.agent import ChatSessionState


async def _org_with_units(db_session: AsyncSession, unit_names=("Cardiology", "Neurology")):
    org = Organization(
        name="WA Catalog Org", api_key=f"ds_{uuid.uuid4().hex[:12]}", is_active=True
    )
    db_session.add(org)
    await db_session.flush()
    db_session.add(OrgConfig(org_id=org.id, required_fields=["name"]))
    for i, name in enumerate(unit_names):
        unit = ServiceUnit(org_id=org.id, name=name, is_active=True)
        db_session.add(unit)
        await db_session.flush()
        prov = Provider(
            org_id=org.id, full_name=f"Dr. Who{i}", is_active=True,
            role_label="Consultant",
        )
        db_session.add(prov)
        await db_session.flush()
        db_session.add(
            UnitProvider(org_id=org.id, unit_id=unit.id, provider_id=prov.id)
        )
    await db_session.commit()
    await db_session.refresh(org)
    return org


def _wa_session(**kw) -> ChatSessionState:
    base = dict(
        session_id=str(uuid.uuid4()),
        org_id=str(uuid.uuid4()),
        channel="whatsapp",
        state_data={},
        collected_data={},
        missing_fields=["name"],
        current_intent="START_BOOKING",
        session_status="ACTIVE",
    )
    base.update(kw)
    return ChatSessionState(**base)


def test_is_whatsapp_channel():
    assert wc.is_whatsapp_channel("whatsapp") is True
    assert wc.is_whatsapp_channel("WhatsApp_Cloud") is True
    assert wc.is_whatsapp_channel("web") is False
    assert wc.is_whatsapp_channel(None) is False


async def test_department_list_offered_on_whatsapp(db_session: AsyncSession):
    org = await _org_with_units(db_session)
    sess = _wa_session()
    action = await wc.maybe_handle_department_pick(
        db_session, org, sess, "I want to book an appointment"
    )
    assert action is not None
    assert "Which department" in action.message
    assert "Cardiology" in action.message
    assert "Neurology" in action.message
    # Offered as a numbered plain-text list (WhatsApp has no UI).
    assert "1. Cardiology" in action.message
    assert "2. Neurology" in action.message
    assert action.widget_directive == "TEXT_INPUT"


async def test_department_pick_by_number_pins_unit(db_session: AsyncSession):
    org = await _org_with_units(db_session)
    sess = _wa_session()
    # First turn: the list is offered.
    first = await wc.maybe_handle_department_pick(
        db_session, org, sess, "I want to book"
    )
    assert first is not None
    # Second turn: the customer replies "1".
    second = await wc.maybe_handle_department_pick(db_session, org, sess, "1")
    assert second is None  # flow continues
    assert sess.collected_data.get("unit_id")
    assert sess.collected_data.get("unit_name") == "Cardiology"


async def test_department_pick_by_name_pins_unit(db_session: AsyncSession):
    org = await _org_with_units(db_session)
    sess = _wa_session()
    await wc.maybe_handle_department_pick(db_session, org, sess, "hi")
    assert await wc.maybe_handle_department_pick(
        db_session, org, sess, "Neurology"
    ) is None
    assert sess.collected_data.get("unit_name") == "Neurology"


async def test_no_department_list_for_individual_business(db_session: AsyncSession):
    org = Organization(
        name="Solo", api_key=f"ds_{uuid.uuid4().hex[:12]}", is_active=True
    )
    db_session.add(org)
    await db_session.flush()
    db_session.add(OrgConfig(org_id=org.id, required_fields=["name"]))
    await db_session.commit()
    sess = _wa_session()
    action = await wc.maybe_handle_department_pick(
        db_session, org, sess, "I want to book"
    )
    assert action is None  # no catalog → no question


async def test_department_list_not_offered_on_web(db_session: AsyncSession):
    org = await _org_with_units(db_session)
    sess = _wa_session(channel="web")
    action = await wc.maybe_handle_department_pick(
        db_session, org, sess, "I want to book"
    )
    assert action is None  # widget has its own dropdown


async def test_mid_collection_does_not_interrupt(db_session: AsyncSession):
    """Once the customer gave details, the department question must not
    hijack the flow."""
    org = await _org_with_units(db_session)
    sess = _wa_session(collected_data={"name": "Aman", "email": "a@b.c"})
    action = await wc.maybe_handle_department_pick(
        db_session, org, sess, "a@b.c"
    )
    assert action is None


async def test_team_member_list_plain_text(db_session: AsyncSession):
    org = await _org_with_units(db_session)
    unit = (
        await db_session.execute(
            select(ServiceUnit).where(ServiceUnit.org_id == org.id)
        )
    ).scalars().first()
    text, members = await wc.offer_team_members(db_session, org, unit.id)
    assert members
    assert "Dr. Who0" in text
    assert text.startswith("1. ")
