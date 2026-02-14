import uuid
from pathlib import Path
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, File, Query, UploadFile
from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.exceptions import NotFoundError
from app.db.database import get_db
from app.dependencies import get_connection_manager, get_current_user, get_qdrant, get_redis
from app.services.connection_manager import ConnectionManager
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.schemas.chat import (
    ChatMessageRequest,
    ChatMessageResponse,
    ConversationDetailResponse,
    ConversationResponse,
)
from app.services.cache_service import CacheService
from app.services.chat_service import ChatService
from app.services.intent_classifier import IntentClassifier
from app.services.llm_service import LLMService
from app.services.odoo_service import OdooService, create_odoo_adapter
from app.services.rag_engine import RAGEngine

CHAT_ALLOWED_TYPES = {"jpg", "jpeg", "png", "gif", "webp", "pdf", "doc", "docx", "xls", "xlsx"}
IMAGE_TYPES = {"jpg", "jpeg", "png", "gif", "webp"}

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    status: str | None = None,
    channel: str | None = None,
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
):
    """List conversations (for employee panel)."""
    query = select(Conversation).order_by(Conversation.updated_at.desc())

    if status:
        query = query.where(Conversation.status == status)
    if channel:
        query = query.where(Conversation.channel == channel)

    query = query.limit(limit).offset(offset)
    result = await db.execute(query)
    conversations = result.scalars().all()

    response = []
    for conv in conversations:
        # Get message count and last message
        msg_count_result = await db.execute(
            select(func.count(Message.id)).where(
                Message.conversation_id == conv.id
            )
        )
        msg_count = msg_count_result.scalar() or 0

        last_msg_result = await db.execute(
            select(Message.content)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg = last_msg_result.scalar()

        # Get assigned agent name if exists
        agent_name = None
        if conv.assigned_agent_id:
            agent_result = await db.execute(
                select(User.full_name).where(User.id == conv.assigned_agent_id)
            )
            agent_name = agent_result.scalar()

        response.append(
            ConversationResponse(
                id=str(conv.id),
                channel=conv.channel,
                status=conv.status,
                mode=conv.mode or "ai",
                visitor_id=conv.visitor_id,
                assigned_agent_id=str(conv.assigned_agent_id) if conv.assigned_agent_id else None,
                assigned_agent_name=agent_name,
                message_count=msg_count,
                last_message=last_msg[:100] if last_msg else None,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
            )
        )

    return response


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get conversation details with messages."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Sohbet bulunamadı")

    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.asc())
    )
    messages = msg_result.scalars().all()

    return ConversationDetailResponse(
        id=str(conv.id),
        channel=conv.channel,
        status=conv.status,
        mode=conv.mode or "ai",
        visitor_id=conv.visitor_id,
        assigned_agent_id=str(conv.assigned_agent_id) if conv.assigned_agent_id else None,
        messages=[
            ChatMessageResponse(
                id=str(m.id),
                conversation_id=str(m.conversation_id),
                role=m.role,
                content=m.content,
                intent=m.intent,
                sources=m.sources or [],
                created_at=m.created_at,
            )
            for m in messages
        ],
        created_at=conv.created_at,
    )


@router.post("/message")
async def send_message(
    body: ChatMessageRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    qdrant: Annotated[AsyncQdrantClient, Depends(get_qdrant)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Send a message from employee panel (non-streaming REST)."""
    rag_engine = RAGEngine(qdrant)
    llm_service = LLMService()
    cache = CacheService(redis_client)

    odoo_service = None
    if settings.odoo_url:
        adapter = create_odoo_adapter()
        odoo_service = OdooService(adapter, cache)

    classifier = IntentClassifier(llm_service)

    chat = ChatService(
        db=db,
        rag_engine=rag_engine,
        llm_service=llm_service,
        odoo_service=odoo_service,
        intent_classifier=classifier,
    )

    result = await chat.process_message(
        user_message=body.content,
        conversation_id=body.conversation_id,
        user_id=str(user.id),
        channel="panel",
    )

    return result


@router.post("/escalate/{conversation_id}")
async def escalate_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cm: Annotated[ConnectionManager, Depends(get_connection_manager)],
):
    """Escalate a conversation to human agent queue."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Sohbet bulunamadı")

    from datetime import datetime, timezone
    conv.status = "waiting"
    conv.escalated_at = datetime.now(timezone.utc)
    await db.commit()

    # Get last message for preview
    last_msg_result = await db.execute(
        select(Message.content)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    last_msg = last_msg_result.scalar() or ""

    # Try auto-assignment first
    assignment = await cm.try_auto_assign(conversation_id, db)

    if assignment:
        # Auto-assigned to an agent
        await cm.notify_conversation_update(conversation_id, "auto_assigned")

        # Notify customer
        await cm.send_to_widget(conversation_id, {
            "type": "system",
            "content": "Bir temsilciye bağlandınız.",
            "event": "agent_joined",
        })

        return {
            "status": "assigned",
            "conversation_id": conversation_id,
            "agent_name": assignment["agent_name"],
        }

    # No agent available - add to queue
    await cm.add_to_queue(conversation_id, {
        "visitor_id": conv.visitor_id or "",
        "last_message": last_msg,
        "source_group_id": str(conv.source_group_id) if conv.source_group_id else "",
        "channel": conv.channel,
    })
    await cm.notify_new_escalation(
        conversation_id, last_msg,
        str(conv.source_group_id) if conv.source_group_id else None,
    )

    # Notify customer
    await cm.send_to_widget(conversation_id, {
        "type": "system",
        "content": "Bir temsilciye bağlanıyorsunuz, lütfen bekleyin...",
        "event": "escalated",
    })

    return {"status": "waiting", "conversation_id": conversation_id}


@router.post("/upload")
async def upload_chat_attachment(
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
):
    """Upload a file attachment for chat messages. Public endpoint (widget users)."""
    if not file.filename:
        raise NotFoundError("Dosya adi gerekli")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in CHAT_ALLOWED_TYPES:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail=f"Desteklenmeyen dosya tipi: {ext}",
        )

    content = await file.read()
    max_size = 10 * 1024 * 1024  # 10MB for chat attachments
    if len(content) > max_size:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Dosya boyutu 10MB'dan buyuk olamaz")

    upload_dir = Path(settings.upload_dir) / "chat"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = str(uuid.uuid4())
    file_path = upload_dir / f"{file_id}.{ext}"

    with open(file_path, "wb") as f:
        f.write(content)

    file_url = f"/uploads/chat/{file_id}.{ext}"

    return {
        "url": file_url,
        "filename": file.filename,
        "size": len(content),
        "type": "image" if ext in IMAGE_TYPES else "file",
        "mime_type": file.content_type or "application/octet-stream",
    }
