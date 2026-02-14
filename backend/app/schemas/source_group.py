import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SourceGroupBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100, pattern="^[a-z0-9_-]+$")
    description: str | None = None
    color: str = Field(default="#6b7280", max_length=20)
    data_permissions: dict = Field(default_factory=dict)
    is_active: bool = True


class SourceGroupCreate(SourceGroupBase):
    pass


class SourceGroupUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    color: str | None = Field(None, max_length=20)
    data_permissions: dict | None = None
    is_active: bool | None = None


class SourceGroupResponse(SourceGroupBase):
    id: uuid.UUID
    is_default: bool = False
    document_count: int = 0
    widget_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SourceGroupListResponse(BaseModel):
    groups: list[SourceGroupResponse]
    total: int
