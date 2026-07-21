from app.models.session import ChatSession
from app.models.booking import Booking
from app.models.config import OrgConfig


def test_chat_session_has_org_updated_index():
    index_names = {idx.name for idx in ChatSession.__table__.indexes}
    assert "ix_session_org_updated" in index_names


def test_booking_has_org_created_index():
    index_names = {idx.name for idx in Booking.__table__.indexes}
    assert "ix_booking_org_created" in index_names


def test_chat_session_has_archived_at_column():
    assert "archived_at" in ChatSession.__table__.columns


def test_org_config_has_session_retention_and_cleanup_fields():
    columns = OrgConfig.__table__.columns
    assert "session_retention_days" in columns
    assert "session_cleanup_frequency" in columns
    assert "session_cleanup_hour_utc" in columns

