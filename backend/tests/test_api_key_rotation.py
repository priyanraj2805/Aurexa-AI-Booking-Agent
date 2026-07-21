"""
Tests for API key rotation with grace period.

SEC-01: API key rotation must allow dual-key validation during grace period.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization
from app.crud.organization import validate_api_key, rotate_api_key, _generate_api_key


class TestOrganizationModelRotationFields:
    """Task 1: Verify Organization model has rotation fields."""

    @pytest.mark.asyncio
    async def test_organization_has_old_api_key_field(self, db_session: AsyncSession):
        """Organization model can store old_api_key (nullable String)."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Org",
            api_key=_generate_api_key(),
            is_active=True,
            old_api_key=None,  # Should be nullable
        )
        db_session.add(org)
        await db_session.commit()
        await db_session.refresh(org)

        assert org.old_api_key is None

        # Set old_api_key
        org.old_api_key = "ds_oldkey12345"
        await db_session.commit()
        await db_session.refresh(org)

        assert org.old_api_key == "ds_oldkey12345"

    @pytest.mark.asyncio
    async def test_organization_has_old_key_expires_at_field(self, db_session: AsyncSession):
        """Organization model can store old_key_expires_at (nullable DateTime with timezone)."""
        org = Organization(
            id=uuid.uuid4(),
            name="Test Org Expiry",
            api_key=_generate_api_key(),
            is_active=True,
            old_key_expires_at=None,  # Should be nullable
        )
        db_session.add(org)
        await db_session.commit()
        await db_session.refresh(org)

        assert org.old_key_expires_at is None

        # Set expiry
        expiry = datetime.now(timezone.utc) + timedelta(days=7)
        org.old_key_expires_at = expiry
        await db_session.commit()
        await db_session.refresh(org)

        assert org.old_key_expires_at is not None


class TestValidateApiKey:
    """Task 2: Validate API key with grace period support."""

    @pytest.mark.asyncio
    async def test_validate_api_key_returns_org_for_current_key(self, db_session: AsyncSession):
        """validate_api_key returns org when current api_key matches."""
        api_key = _generate_api_key()
        org = Organization(
            id=uuid.uuid4(),
            name="Current Key Org",
            api_key=api_key,
            is_active=True,
        )
        db_session.add(org)
        await db_session.commit()

        result = await validate_api_key(db_session, api_key)
        assert result is not None
        assert result.id == org.id

    @pytest.mark.asyncio
    async def test_validate_api_key_returns_org_for_valid_old_key(self, db_session: AsyncSession):
        """validate_api_key returns org when old_api_key matches AND not expired."""
        current_key = _generate_api_key()
        old_key = _generate_api_key()
        org = Organization(
            id=uuid.uuid4(),
            name="Old Key Org",
            api_key=current_key,
            old_api_key=old_key,
            old_key_expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            is_active=True,
        )
        db_session.add(org)
        await db_session.commit()

        result = await validate_api_key(db_session, old_key)
        assert result is not None
        assert result.id == org.id

    @pytest.mark.asyncio
    async def test_validate_api_key_returns_none_for_expired_old_key(self, db_session: AsyncSession):
        """validate_api_key returns None when old_api_key matches BUT expired."""
        current_key = _generate_api_key()
        old_key = _generate_api_key()
        org = Organization(
            id=uuid.uuid4(),
            name="Expired Key Org",
            api_key=current_key,
            old_api_key=old_key,
            old_key_expires_at=datetime.now(timezone.utc) - timedelta(days=1),  # Expired
            is_active=True,
        )
        db_session.add(org)
        await db_session.commit()

        result = await validate_api_key(db_session, old_key)
        assert result is None

    @pytest.mark.asyncio
    async def test_validate_api_key_returns_none_for_unknown_key(self, db_session: AsyncSession):
        """validate_api_key returns None for unknown API key."""
        result = await validate_api_key(db_session, "ds_unknownkey12345")
        assert result is None


class TestRotateApiKey:
    """Task 2: Rotate API key with grace period."""

    @pytest.mark.asyncio
    async def test_rotate_api_key_generates_new_key(self, db_session: AsyncSession):
        """rotate_api_key generates new key, moves current to old."""
        original_key = _generate_api_key()
        org = Organization(
            id=uuid.uuid4(),
            name="Rotate Org",
            api_key=original_key,
            is_active=True,
        )
        db_session.add(org)
        await db_session.commit()

        rotated_org = await rotate_api_key(db_session, str(org.id))

        # New key should be different
        assert rotated_org.api_key != original_key
        assert rotated_org.api_key.startswith("ds_")

        # Old key should be preserved
        assert rotated_org.old_api_key == original_key

        # Expiry should be set
        assert rotated_org.old_key_expires_at is not None

    @pytest.mark.asyncio
    async def test_rotate_api_key_sets_default_grace_period(self, db_session: AsyncSession):
        """rotate_api_key sets default 7-day grace period."""
        original_key = _generate_api_key()
        org = Organization(
            id=uuid.uuid4(),
            name="Default Grace Org",
            api_key=original_key,
            is_active=True,
        )
        db_session.add(org)
        await db_session.commit()

        before = datetime.now(timezone.utc)
        rotated_org = await rotate_api_key(db_session, str(org.id))
        after = datetime.now(timezone.utc)

        # Expiry should be ~7 days from now
        expected_min = before + timedelta(days=7)
        expected_max = after + timedelta(days=7)

        # Handle SQLite returning naive datetime in tests
        expires_at = rotated_org.old_key_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expected_min_naive = expected_min.replace(tzinfo=None)
        expected_max_naive = expected_max.replace(tzinfo=None)
        expires_at_naive = rotated_org.old_key_expires_at.replace(tzinfo=None) if rotated_org.old_key_expires_at.tzinfo else rotated_org.old_key_expires_at

        assert expires_at_naive >= expected_min_naive
        assert expires_at_naive <= expected_max_naive

    @pytest.mark.asyncio
    async def test_rotate_api_key_accepts_custom_grace_period(self, db_session: AsyncSession):
        """rotate_api_key accepts custom grace_period_days."""
        original_key = _generate_api_key()
        org = Organization(
            id=uuid.uuid4(),
            name="Custom Grace Org",
            api_key=original_key,
            is_active=True,
        )
        db_session.add(org)
        await db_session.commit()

        before = datetime.now(timezone.utc)
        rotated_org = await rotate_api_key(db_session, str(org.id), grace_period_days=14)
        after = datetime.now(timezone.utc)

        # Expiry should be ~14 days from now
        expected_min_naive = (before + timedelta(days=14)).replace(tzinfo=None)
        expected_max_naive = (after + timedelta(days=14)).replace(tzinfo=None)
        expires_at_naive = rotated_org.old_key_expires_at.replace(tzinfo=None) if rotated_org.old_key_expires_at.tzinfo else rotated_org.old_key_expires_at

        assert expires_at_naive >= expected_min_naive
        assert expires_at_naive <= expected_max_naive

    @pytest.mark.asyncio
    async def test_rotate_api_key_raises_for_unknown_org(self, db_session: AsyncSession):
        """rotate_api_key raises ValueError for unknown org."""
        with pytest.raises(ValueError, match="Organization not found"):
            await rotate_api_key(db_session, str(uuid.uuid4()))
