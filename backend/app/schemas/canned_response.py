from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class CannedResponseCreate(BaseModel):
    title: str
    content: str
    category: str = "genel"
    scope: str = "global"
    shortcut: Optional[str] = None


class CannedResponseUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    shortcut: Optional[str] = None
    is_active: Optional[bool] = None


class CannedResponseResponse(BaseModel):
    id: str
    title: str
    content: str
    category: str
    scope: str
    shortcut: Optional[str] = None
    owner_id: str
    owner_name: Optional[str] = None
    is_active: bool
    usage_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
