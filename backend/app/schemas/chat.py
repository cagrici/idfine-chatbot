from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ChatMessageRequest(BaseModel):
    content: str
    conversation_id: Optional[str] = None
    source_group_id: Optional[str] = None


class ChatMessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    intent: Optional[str] = None
    sources: list = []
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    id: str
    channel: str
    status: str
    mode: str = "ai"
    visitor_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    assigned_agent_name: Optional[str] = None
    message_count: int = 0
    last_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConversationDetailResponse(BaseModel):
    id: str
    channel: str
    status: str
    mode: str = "ai"
    visitor_id: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    messages: list[ChatMessageResponse] = []
    created_at: datetime

    model_config = {"from_attributes": True}


# WebSocket message types
class WSMessage(BaseModel):
    type: str  # message, typing, ping
    content: Optional[str] = None
    conversation_id: Optional[str] = None


class WSStreamStart(BaseModel):
    type: str = "stream_start"
    message_id: str


class WSStreamChunk(BaseModel):
    type: str = "stream_chunk"
    content: str
    message_id: str


class WSStreamEnd(BaseModel):
    type: str = "stream_end"
    message_id: str
    sources: list = []
    intent: Optional[str] = None


class WSError(BaseModel):
    type: str = "error"
    message: str
