import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class WidgetConfigBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str = Field(min_length=1, max_length=500)
    source_group_id: uuid.UUID | None = None
    logo_variant: str = Field(default="dark", pattern="^(dark|light)$")
    brand_color: str = Field(default="#231f20", max_length=20)
    brand_name: str = Field(default="ID Fine", max_length=100)
    welcome_message: str = Field(
        default="Merhaba! Size nasıl yardımcı olabilirim?"
    )
    placeholder: str = Field(default="Mesajınızı yazın...", max_length=200)
    position: str = Field(
        default="bottom-right", pattern="^(bottom-right|bottom-left)$"
    )
    width: int = Field(default=380, ge=300, le=600)
    height: int = Field(default=560, ge=400, le=800)
    trigger_size: int = Field(default=60, ge=40, le=100)
    is_active: bool = True


class WidgetConfigCreate(WidgetConfigBase):
    pass


class WidgetConfigUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    domain: str | None = Field(None, min_length=1, max_length=500)
    source_group_id: uuid.UUID | None = None
    logo_variant: str | None = Field(None, pattern="^(dark|light)$")
    brand_color: str | None = Field(None, max_length=20)
    brand_name: str | None = Field(None, max_length=100)
    welcome_message: str | None = None
    placeholder: str | None = Field(None, max_length=200)
    position: str | None = Field(None, pattern="^(bottom-right|bottom-left)$")
    width: int | None = Field(None, ge=300, le=600)
    height: int | None = Field(None, ge=400, le=800)
    trigger_size: int | None = Field(None, ge=40, le=100)
    is_active: bool | None = None


class WidgetConfigResponse(WidgetConfigBase):
    id: uuid.UUID
    source_group_name: str | None = None
    created_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WidgetConfigListResponse(BaseModel):
    configs: list[WidgetConfigResponse]
    total: int
