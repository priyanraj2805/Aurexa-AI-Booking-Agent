from pydantic import BaseModel, UUID4, ConfigDict, field_validator
from datetime import datetime
from typing import Optional

class OrgConfigBase(BaseModel):
    required_fields: list[str] = ["name", "email", "phone", "date", "time"]
    custom_prompts: Optional[list[str]] = None
    timezone: str = "UTC"
    ui_brand_color: str = "#3B82F6"

class OrganizationBase(BaseModel):
    name: str
    is_active: bool = True

class OrganizationCreate(OrganizationBase):
    pass

class OrgConfigResponse(OrgConfigBase):
    org_id: UUID4

    model_config = ConfigDict(from_attributes=True)

class OrganizationResponse(OrganizationBase):
    id: UUID4
    api_key: str
    type: str = "individual"
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# API key rotation schemas (SEC-01)
class RotateApiKeyRequest(BaseModel):
    """Request body for API key rotation."""
    grace_period_days: int = 7
    
    @field_validator('grace_period_days')
    @classmethod
    def validate_grace_period(cls, v):
        if v < 0 or v > 30:
            raise ValueError('Grace period must be between 0 and 30 days')
        return v


class RotateApiKeyResponse(BaseModel):
    """Response for API key rotation."""
    new_api_key: str
    old_key_expires_at: datetime
    message: str
    
    model_config = ConfigDict(from_attributes=True)
