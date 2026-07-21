import secrets
import string
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import Optional
from app.models.organization import Organization
from app.models.config import OrgConfig
from app.schemas.organization import OrganizationCreate, OrgConfigBase


# Default grace period for API key rotation (SEC-01)
DEFAULT_GRACE_PERIOD_DAYS = 7


def _generate_api_key(length: int = 32) -> str:
    alphabet = string.ascii_letters + string.digits
    return "ds_" + "".join(secrets.choice(alphabet) for i in range(length))

async def create_organization(db: AsyncSession, org_in: OrganizationCreate) -> Organization:
    db_org = Organization(
        name=org_in.name,
        is_active=org_in.is_active,
        api_key=_generate_api_key()
    )
    db.add(db_org)
    await db.commit()
    await db.refresh(db_org)
    
    # Create default config
    default_config = OrgConfig(
        org_id=db_org.id,
        required_fields=["name", "email", "phone", "date", "time"]
    )
    db.add(default_config)
    await db.commit()
    
    return db_org

async def get_organization(db: AsyncSession, org_id: str) -> Optional[Organization]:
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    return result.scalars().first()


async def validate_api_key(db: AsyncSession, api_key: str) -> Optional[Organization]:
    """
    Validate API key with grace period support (SEC-01).
    
    Checks current api_key first, then old_api_key if within grace period.
    This allows both old and new keys to work during the rotation transition.
    
    Args:
        db: Database session
        api_key: The API key to validate
        
    Returns:
        Organization if valid key found, None otherwise
    """
    # Check current key first
    result = await db.execute(
        select(Organization).where(
            Organization.api_key == api_key,
            Organization.is_active == True
        )
    )
    org = result.scalars().first()
    if org:
        return org
    
    # Check old key (grace period) - only if not expired
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(Organization).where(
            Organization.old_api_key == api_key,
            Organization.is_active == True,
            Organization.old_key_expires_at > now
        )
    )
    return result.scalars().first()


async def rotate_api_key(
    db: AsyncSession, 
    org_id: str, 
    grace_period_days: int = DEFAULT_GRACE_PERIOD_DAYS
) -> Organization:
    """
    Rotate API key with grace period (SEC-01).
    
    - Current key becomes old_api_key
    - New key generated for api_key
    - Grace period set for old key validity
    
    Args:
        db: Database session
        org_id: Organization UUID
        grace_period_days: How long old key remains valid (default 7)
        
    Returns:
        Updated Organization with new key
        
    Raises:
        ValueError: If organization not found
    """
    org = await get_organization(db, org_id)
    if not org:
        raise ValueError("Organization not found")
    
    # Move current key to old
    org.old_api_key = org.api_key
    org.old_key_expires_at = datetime.now(timezone.utc) + timedelta(days=grace_period_days)
    
    # Generate new key
    org.api_key = _generate_api_key()
    
    await db.commit()
    await db.refresh(org)
    return org
