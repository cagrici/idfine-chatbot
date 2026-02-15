import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.db.database import get_db
from app.dependencies import get_connection_manager, require_permission
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.services.connection_manager import ConnectionManager

router = APIRouter(prefix="/live-support", tags=["live-support"])

require_respond = require_permission("conversations.respond")


@router.get("/queue")
async def get_queue(
    user: Annotated[User, Depends(require_respond)],
    cm: Annotated[ConnectionManager, Depends(get_connection_manager)],
):
    """Get list of conversations waiting for an agent."""
    conversations = await cm.get_waiting_conversations()
    return {"conversations": conversations, "total": len(conversations)}


@router.get("/all")
async def get_all_conversations(
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cm: Annotated[ConnectionManager, Depends(get_connection_manager)],
    limit: int = Query(default=50, le=100),
):
    """Get all recent conversations with online status."""
    result = await db.execute(
        select(
            Conversation,
            User.full_name.label("agent_name"),
        )
        .outerjoin(User, Conversation.assigned_agent_id == User.id)
        .where(Conversation.status.in_(["active", "assigned", "waiting"]))
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
    )
    rows = result.all()

    response = []
    for conv, agent_name in rows:
        conv_id = str(conv.id)
        # Get last message
        last_msg_result = await db.execute(
            select(Message.content, Message.role)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg_row = last_msg_result.first()

        # Get message count
        count_result = await db.execute(
            select(sa_func.count())
            .select_from(Message)
            .where(Message.conversation_id == conv.id)
        )
        msg_count = count_result.scalar() or 0

        response.append({
            "conversation_id": conv_id,
            "visitor_id": conv.visitor_id,
            "channel": conv.channel or "widget",
            "status": conv.status,
            "mode": conv.mode or "ai",
            "source_group_id": str(conv.source_group_id) if conv.source_group_id else None,
            "assigned_agent_name": agent_name,
            "last_message": last_msg_row[0][:200] if last_msg_row else None,
            "last_message_role": last_msg_row[1] if last_msg_row else None,
            "message_count": msg_count,
            "tags": conv.tags or [],
            "online": cm.has_widget_connection(conv_id),
            "escalated_at": conv.escalated_at.isoformat() if conv.escalated_at else None,
            "first_response_at": conv.first_response_at.isoformat() if conv.first_response_at else None,
            "updated_at": conv.updated_at.isoformat() if conv.updated_at else None,
        })

    return {"conversations": response, "total": len(response)}


@router.get("/active")
async def get_active_conversations(
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get conversations currently assigned to this agent."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.assigned_agent_id == user.id)
        .where(Conversation.mode == "human")
        .order_by(Conversation.updated_at.desc())
    )
    conversations = result.scalars().all()

    response = []
    for conv in conversations:
        # Get last message
        last_msg_result = await db.execute(
            select(Message.content)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        last_msg = last_msg_result.scalar()

        response.append({
            "conversation_id": str(conv.id),
            "visitor_id": conv.visitor_id,
            "channel": conv.channel,
            "status": conv.status,
            "source_group_id": str(conv.source_group_id) if conv.source_group_id else None,
            "last_message": last_msg[:200] if last_msg else None,
        })

    return {"conversations": response, "total": len(response)}


@router.get("/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get conversation messages without claiming (read-only view)."""
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

    return {
        "conversation_id": conversation_id,
        "status": conv.status,
        "mode": conv.mode or "ai",
        "assigned_agent_id": str(conv.assigned_agent_id) if conv.assigned_agent_id else None,
        "visitor_id": conv.visitor_id,
        "channel": conv.channel or "widget",
        "tags": conv.tags or [],
        "messages": [
            {
                "id": str(m.id),
                "role": m.role,
                "content": m.content,
                "sender_type": m.sender_type or "ai",
                "agent_id": str(m.agent_id) if m.agent_id else None,
                "attachments": m.attachments,
                "feedback": m.feedback,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ],
    }


@router.post("/claim/{conversation_id}")
async def claim_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cm: Annotated[ConnectionManager, Depends(get_connection_manager)],
):
    """Claim a waiting conversation (assign to this agent)."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Sohbet bulunamadı")

    if conv.mode == "human" and conv.assigned_agent_id and conv.assigned_agent_id != user.id:
        from app.core.exceptions import ConflictError
        raise ConflictError("Bu sohbet başka bir temsilci tarafından devralınmış")

    from datetime import datetime, timezone
    conv.status = "assigned"
    conv.mode = "human"
    conv.assigned_agent_id = user.id
    if not conv.first_response_at:
        conv.first_response_at = datetime.now(timezone.utc)
    await db.commit()

    # Remove from queue
    await cm.remove_from_queue(conversation_id)

    # Notify customer that an agent joined
    from app.services.meta_sender import SOCIAL_CHANNELS
    if conv.channel in SOCIAL_CHANNELS:
        from app.services.meta_sender import get_meta_sender, get_social_recipient
        recipient = get_social_recipient(conv)
        if recipient:
            sender = get_meta_sender()
            await sender.send_message(conv.channel, recipient, "Bir temsilci sohbete katıldı.")
    else:
        await cm.send_to_widget(conversation_id, {
            "type": "system",
            "content": "Bir temsilci sohbete katıldı.",
            "event": "agent_joined",
        })

    # Notify other agents that queue changed
    await cm.notify_conversation_update(conversation_id, "claimed")

    return {
        "status": "assigned",
        "conversation_id": conversation_id,
        "agent_id": str(user.id),
    }


@router.post("/release/{conversation_id}")
async def release_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cm: Annotated[ConnectionManager, Depends(get_connection_manager)],
):
    """Release a conversation back to AI mode."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Sohbet bulunamadı")

    conv.status = "active"
    conv.mode = "ai"
    conv.assigned_agent_id = None
    await db.commit()

    # Notify customer that agent left
    from app.services.meta_sender import SOCIAL_CHANNELS
    if conv.channel in SOCIAL_CHANNELS:
        from app.services.meta_sender import get_meta_sender, get_social_recipient
        recipient = get_social_recipient(conv)
        if recipient:
            sender = get_meta_sender()
            await sender.send_message(conv.channel, recipient, "Temsilci sohbetten ayrıldı. AI asistan tekrar aktif.")
    else:
        await cm.send_to_widget(conversation_id, {
            "type": "system",
            "content": "Temsilci sohbetten ayrıldı. AI asistan tekrar aktif.",
            "event": "agent_left",
        })

    # Notify agents
    await cm.notify_conversation_update(conversation_id, "released")

    return {"status": "active", "conversation_id": conversation_id}


@router.post("/close/{conversation_id}")
async def close_conversation(
    conversation_id: str,
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cm: Annotated[ConnectionManager, Depends(get_connection_manager)],
):
    """Close a conversation and request CSAT rating from the customer."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Sohbet bulunamadi")

    conv.status = "closed"
    conv.mode = "ai"
    conv.assigned_agent_id = None
    await db.commit()

    # Send rating request to customer
    from app.services.meta_sender import SOCIAL_CHANNELS
    if conv.channel in SOCIAL_CHANNELS:
        from app.services.meta_sender import get_meta_sender, get_social_recipient
        recipient = get_social_recipient(conv)
        if recipient:
            sender = get_meta_sender()
            await sender.send_message(conv.channel, recipient, "Sohbet kapatildi. Hizmetimizi 1-5 arasi puanlayabilirsiniz.")
    else:
        await cm.send_to_widget(conversation_id, {
            "type": "system",
            "content": "Sohbet kapatildi. Hizmetimizi degerlendirin.",
            "event": "request_rating",
            "conversation_id": conversation_id,
        })

    await cm.notify_conversation_update(conversation_id, "closed")

    return {"status": "closed", "conversation_id": conversation_id}


@router.post("/{conversation_id}/note")
async def add_note(
    conversation_id: str,
    request: Request,
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cm: Annotated[ConnectionManager, Depends(get_connection_manager)],
):
    """Add an internal note to a conversation (only visible to agents)."""
    body = await request.json()
    content = body.get("content", "").strip()
    if not content:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Not icerigi bos olamaz")

    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Sohbet bulunamadi")

    note = Message(
        conversation_id=conv.id,
        role="assistant",
        content=content,
        sender_type="note",
        agent_id=user.id,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)

    # Notify agents viewing this conversation
    await cm.send_to_agent(conversation_id, {
        "type": "note",
        "content": content,
        "agent_name": user.full_name,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    })

    return {
        "status": "ok",
        "note_id": str(note.id),
        "content": content,
        "agent_name": user.full_name,
    }


@router.put("/{conversation_id}/tags")
async def update_tags(
    conversation_id: str,
    request: Request,
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
    cm: Annotated[ConnectionManager, Depends(get_connection_manager)],
):
    """Update tags for a conversation."""
    body = await request.json()
    tags = body.get("tags", [])
    if not isinstance(tags, list):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Tags must be a list")

    # Normalize: lowercase, strip, unique, max 10
    tags = list(dict.fromkeys(t.strip().lower() for t in tags if isinstance(t, str) and t.strip()))[:10]

    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Sohbet bulunamadi")

    conv.tags = tags
    await db.commit()

    # Notify agents
    await cm.notify_conversation_update(conversation_id, "tags_updated")

    return {"status": "ok", "conversation_id": conversation_id, "tags": tags}


@router.get("/{conversation_id}/profile")
async def get_visitor_profile(
    conversation_id: str,
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get customer profile data for a conversation's visitor."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise NotFoundError("Sohbet bulunamadi")

    visitor_id = conv.visitor_id

    # Total conversations by this visitor
    total_convs = (await db.execute(
        select(sa_func.count(Conversation.id))
        .where(Conversation.visitor_id == visitor_id)
    )).scalar() or 0

    # First seen
    first_seen = (await db.execute(
        select(sa_func.min(Conversation.created_at))
        .where(Conversation.visitor_id == visitor_id)
    )).scalar()

    # Total messages from this visitor
    total_msgs = (await db.execute(
        select(sa_func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(Conversation.visitor_id == visitor_id)
        .where(Message.role == "user")
    )).scalar() or 0

    # Average rating
    avg_rating = (await db.execute(
        select(sa_func.avg(Conversation.rating))
        .where(Conversation.visitor_id == visitor_id)
        .where(Conversation.rating.isnot(None))
    )).scalar()

    # Previous conversations
    prev_convs = (await db.execute(
        select(Conversation.id, Conversation.channel, Conversation.status, Conversation.created_at, Conversation.tags)
        .where(Conversation.visitor_id == visitor_id)
        .order_by(Conversation.created_at.desc())
        .limit(5)
    )).all()

    return {
        "visitor_id": visitor_id,
        "channel": conv.channel,
        "total_conversations": total_convs,
        "total_messages": total_msgs,
        "first_seen": first_seen.isoformat() if first_seen else None,
        "avg_rating": round(avg_rating, 1) if avg_rating else None,
        "metadata": conv.metadata_ or {},
        "previous_conversations": [
            {
                "id": str(c.id),
                "channel": c.channel,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "tags": c.tags or [],
            }
            for c in prev_convs
        ],
    }


@router.post("/message/{message_id}/feedback")
async def submit_feedback(
    message_id: str,
    request: Request,
    user: Annotated[User, Depends(require_respond)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Submit feedback (good/bad) on an AI message for training."""
    body = await request.json()
    feedback = body.get("feedback", "")
    note = body.get("note", "")

    if feedback not in ("good", "bad"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Feedback must be 'good' or 'bad'")

    result = await db.execute(
        select(Message).where(Message.id == uuid.UUID(message_id))
    )
    msg = result.scalar_one_or_none()
    if not msg:
        raise NotFoundError("Mesaj bulunamadi")

    msg.feedback = feedback
    msg.feedback_note = note[:500] if note else None
    await db.commit()

    return {"status": "ok", "message_id": message_id, "feedback": feedback}
