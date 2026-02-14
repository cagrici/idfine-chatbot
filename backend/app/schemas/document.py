from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    file_size: Optional[int] = None
    category: Optional[str] = None
    source_group_id: Optional[str] = None
    source_group_name: Optional[str] = None
    status: str
    chunk_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    status: str
    message: str


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
    total: int
