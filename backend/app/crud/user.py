from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.user import User
from app.models.organization import Organization
from app.models.config import OrgConfig
from app.core.security import hash_password, verify_password
from app.schemas.auth import UserRegister
from app.crud.organization import _generate_api_key


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()


async def authenticate_user(db: AsyncSession, email: str, password: str) -> Optional[User]:
    user = await get_user_by_email(db, email)
    if not user or not verify_password(password, user.hashed_password):
        return None
    if not user.is_active:
        return None
    return user


async def create_user_with_org(db: AsyncSession, data: UserRegister) -> tuple:
    """
    Registration flow:
    1. Create Organization
    2. Create default OrgConfig
    3. Create User linked to org
    Returns (user, org)
    """
    # 1. Create org (individual shop vs organization mall)
    org_type = getattr(data, "org_type", "individual") or "individual"
    if org_type not in ("individual", "organization"):
        org_type = "individual"
    org = Organization(
        name=data.org_name,
        api_key=_generate_api_key(),
        is_active=True,
        type=org_type,
    )
    db.add(org)
    await db.flush()  # get org.id without committing

    # 2. Default config
    config = OrgConfig(
        org_id=org.id,
        required_fields=["name", "email", "phone", "date", "time"],
    )
    db.add(config)

    # 3. Create user
    user = User(
        email=data.email.lower().strip(),
        hashed_password=hash_password(data.password),
        full_name=data.full_name.strip(),
        org_id=org.id,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await db.refresh(org)

    return user, org


async def update_user_password(
    db: AsyncSession, user: User, new_password: str
) -> None:
    """Set a new password hash for the user (forgot-password flow)."""
    user.hashed_password = hash_password(new_password)
    await db.commit()
    await db.refresh(user)
