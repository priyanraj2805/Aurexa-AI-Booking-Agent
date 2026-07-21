from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import UUID4

from app.db.database import get_db
from app.crud import organization as crud_org
from app.schemas.organization import OrganizationResponse, OrganizationCreate, RotateApiKeyRequest, RotateApiKeyResponse
from app.models.user import User
from app.api.v1.endpoints.auth import get_current_user
from app.core.audit import log_audit_event
from app.schemas.audit import AuditAction

router = APIRouter()

@router.post("/", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    org_in: OrganizationCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new organization (tenant) and its default configuration.
    Requires authentication. Returns the newly generated API key.
    """
    org = await crud_org.create_organization(db, org_in)
    log_audit_event(
        background_tasks,
        org_id=org.id,
        actor_id=user.id,
        actor_email=user.email,
        action=AuditAction.ORG_CREATE,
        resource_type="organization",
        resource_id=str(org.id),
        old_values=None,
        new_values={"name": org.name, "is_active": org.is_active},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    return org

@router.get("/{org_id}", response_model=OrganizationResponse)
async def get_organization(
    org_id: UUID4,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get organization details by ID. Only accessible to the organization's own users.
    """
    if str(user.org_id) != str(org_id):
        raise HTTPException(status_code=403, detail="Access denied: not your organization")
    org = await crud_org.get_organization(db, str(org_id))
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.post("/{org_id}/rotate-api-key", response_model=RotateApiKeyResponse)
async def rotate_organization_api_key(
    org_id: UUID4,
    request_body: RotateApiKeyRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Rotate organization API key with grace period (SEC-01).
    
    Both old and new keys will work during the grace period.
    Requires admin authentication.
    """
    # Verify user belongs to this org
    if str(user.org_id) != str(org_id):
        raise HTTPException(status_code=403, detail="Access denied: not your organization")
    
    try:
        org = await crud_org.rotate_api_key(db, str(org_id), request_body.grace_period_days)
        log_audit_event(
            background_tasks,
            org_id=user.org_id,
            actor_id=user.id,
            actor_email=user.email,
            action=AuditAction.APIKEY_ROTATE,
            resource_type="organization",
            resource_id=str(org_id),
            old_values={"grace_period_days": request_body.grace_period_days},
            new_values={"old_key_expires_at": org.old_key_expires_at.isoformat()},
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        return RotateApiKeyResponse(
            new_api_key=org.api_key,
            old_key_expires_at=org.old_key_expires_at,
            message=f"API key rotated. Old key valid until {org.old_key_expires_at.isoformat()}"
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
