"""WhatsApp department picker — the regression guard for the missing
department/team-member offer on WhatsApp.

The web widget renders a department dropdown BEFORE the chat starts, so
`unit_id` is already pinned there. WhatsApp has no pre-chat UI, so the
list has to be offered in-conversation. Without this, WhatsApp customers
never learned the business had departments or team members.

Nothing here is keyword-driven: the departments come from the DATABASE and
a reply is matched against the real department names.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.engine import whatsapp_catalog as wc
from app.models.config import OrgConfig
from app.models.organization import Organization
from app.models.service_unit import Provider, ServiceUnit, UnitProvider
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
    action = await wc.build_department_offer(db_session, org, sess)
    assert action is not None
    assert "Which department" in action.message
    assert "Cardiology" in action.message
    assert "Neurology" in action.message
    # Offered as a numbered plain-text list (WhatsApp has no UI).
    assert "1. Cardiology" in action.message
    assert "2. Neurology" in action.message
    assert action.widget_directive == "TEXT_INPUT"


async def test_every_department_is_listed(db_session: AsyncSession):
    """A big business must be able to SEE every department — the list is
    never cut to a fixed count."""
    names = tuple(f"Dept {chr(65 + i)}" for i in range(25))
    org = await _org_with_units(db_session, unit_names=names)
    sess = _wa_session()
    action = await wc.build_department_offer(db_session, org, sess)
    assert action is not None
    for i, name in enumerate(names, start=1):
        assert f"{i}. {name}" in action.message


async def test_department_list_offered_only_once_per_session(
    db_session: AsyncSession,
):
    org = await _org_with_units(db_session)
    sess = _wa_session()
    first = await wc.build_department_offer(db_session, org, sess)
    assert first is not None
    second = await wc.build_department_offer(db_session, org, sess)
    assert second is None


async def test_department_pick_by_number_pins_unit(db_session: AsyncSession):
    org = await _org_with_units(db_session)
    sess = _wa_session()
    picked = await wc.try_pick_department(db_session, org, sess, "1")
    assert picked is not None
    assert sess.collected_data.get("unit_id")
    assert sess.collected_data.get("unit_name") == "Cardiology"


async def test_department_pick_by_name_pins_unit(db_session: AsyncSession):
    org = await _org_with_units(db_session)
    sess = _wa_session()
    picked = await wc.try_pick_department(db_session, org, sess, "Neurology")
    assert picked is not None
    assert sess.collected_data.get("unit_name") == "Neurology"


async def test_department_list_not_repeated_once_pinned(
    db_session: AsyncSession,
):
    """Once a department is chosen the list is never shown again."""
    org = await _org_with_units(db_session)
    sess = _wa_session()
    await wc.try_pick_department(db_session, org, sess, "Cardiology")
    assert await wc.build_department_offer(db_session, org, sess) is None


async def test_naming_a_department_again_switches_to_it(
    db_session: AsyncSession,
):
    """A customer who names a different department changes their mind —
    the new department wins."""
    org = await _org_with_units(db_session)
    sess = _wa_session()
    await wc.try_pick_department(db_session, org, sess, "Cardiology")
    picked = await wc.try_pick_department(db_session, org, sess, "Neurology")
    assert picked is not None
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
    action = await wc.build_department_offer(db_session, org, sess)
    assert action is None  # no catalog → no question


async def test_department_list_not_offered_on_web(db_session: AsyncSession):
    org = await _org_with_units(db_session)
    sess = _wa_session(channel="web")
    action = await wc.build_department_offer(db_session, org, sess)
    assert action is None  # widget has its own dropdown


async def test_typing_a_name_picks_a_department(db_session: AsyncSession):
    """The name is matched against ALL departments — nothing is hidden."""
    names = tuple(f"Dept {chr(65 + i)}" for i in range(20))
    org = await _org_with_units(db_session, unit_names=names)
    sess = _wa_session()
    picked = await wc.try_pick_department(db_session, org, sess, "Dept T")
    assert picked is not None
    assert sess.collected_data.get("unit_name") == "Dept T"


async def test_prefix_match_picks_department(db_session: AsyncSession):
    """A partial name ("cardio" for "Cardiology") resolves."""
    org = await _org_with_units(db_session, unit_names=("Cardiology", "Neurology"))
    sess = _wa_session()
    picked = await wc.try_pick_department(db_session, org, sess, "cardio")
    assert picked is not None
    assert sess.collected_data.get("unit_name") == "Cardiology"


async def test_department_name_inside_sentence_is_picked(
    db_session: AsyncSession,
):
    """"do you have dermatology?" resolves to the Dermatology department."""
    org = await _org_with_units(
        db_session, unit_names=("Cardiology", "Dermatology", "Neurology")
    )
    sess = _wa_session()
    picked = await wc.try_pick_department(
        db_session, org, sess, "do you have dermatology?"
    )
    assert picked is not None
    assert sess.collected_data.get("unit_name") == "Dermatology"


async def test_unrelated_message_picks_nothing(db_session: AsyncSession):
    """A message that names no department must not be swallowed."""
    org = await _org_with_units(db_session)
    sess = _wa_session()
    assert await wc.try_pick_department(
        db_session, org, sess, "what are your timings?"
    ) is None
    assert not sess.collected_data.get("unit_id")


def test_detail_captured_from_department_flags_bogus_capture():
    """A reply of "Cardiology" filed under the customer's name is bogus."""
    unit = {"id": "u1", "name": "Cardiology"}
    assert (
        wc.detail_captured_from_department({"name": "Cardiology"}, unit) == "name"
    )
    assert (
        wc.detail_captured_from_department({"name": "cardiology "}, unit) == "name"
    )
    assert wc.detail_captured_from_department({"name": "Rahul"}, unit) is None
    assert wc.detail_captured_from_department({}, unit) is None
    assert wc.detail_captured_from_department(None, unit) is None


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
