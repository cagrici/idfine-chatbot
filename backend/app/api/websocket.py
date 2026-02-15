import asyncio
import json
import logging
import uuid as _uuid

import redis.asyncio as redis
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select

from app.config import Settings, get_settings
from app.core.rate_limiter import RateLimiter
from app.core.security import decode_token
from app.db.database import async_session
from app.dependencies import get_connection_manager
from app.models.conversation import Conversation, Message
from app.models.user import User
from app.services.connection_manager import ConnectionManager
from app.services.cache_service import CacheService
from app.services.chat_service import ChatService
from app.services.conversation_flow import FlowManager, FlowType
from app.services.customer_session_service import CustomerSessionService
from app.services.flows.address_flow import AddressFlowHandler
from app.services.flows.cancel_order_flow import CancelOrderFlowHandler
from app.services.flows.complaint_flow import ComplaintFlowHandler
from app.services.flows.order_flow import OrderFlowHandler
from app.services.flows.otp_flow import OTPFlowHandler
from app.services.flows.quotation_flow import QuotationFlowHandler
from app.services.flows.ticket_flow import TicketFlowHandler
from app.services.intent_classifier import IntentClassifier
from app.services.llm_service import LLMService
from app.services.odoo_service import OdooService, create_odoo_adapter
from app.services.otp_service import OTPService
from app.services.rag_engine import RAGEngine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


async def _create_chat_dependencies(settings: Settings):
    """Create all chat dependencies including customer auth services."""
    qdrant = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    redis_client = redis.from_url(settings.redis_url, decode_responses=True)

    rag_engine = RAGEngine(qdrant)
    llm_service = LLMService()
    cache = CacheService(redis_client)

    odoo_service = None
    odoo_adapter = None
    if settings.odoo_url:
        odoo_adapter = create_odoo_adapter()
        odoo_service = OdooService(odoo_adapter, cache)

    classifier = IntentClassifier(llm_service)

    # Customer auth services
    otp_service = OTPService(redis_client)
    customer_session = CustomerSessionService(redis_client)
    flow_manager = FlowManager(redis_client)

    # Register flow handlers
    if odoo_adapter:
        otp_flow = OTPFlowHandler(otp_service, customer_session, odoo_adapter)
        flow_manager.register_handler(otp_flow)
    if odoo_service:
        flow_manager.register_handler(TicketFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(ComplaintFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(CancelOrderFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(AddressFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(OrderFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(QuotationFlowHandler(odoo_service, customer_session))

    return {
        "rag_engine": rag_engine,
        "llm_service": llm_service,
        "odoo_service": odoo_service,
        "classifier": classifier,
        "qdrant": qdrant,
        "redis_client": redis_client,
        "flow_manager": flow_manager,
        "customer_session": customer_session,
        "otp_service": otp_service,
    }


@router.websocket("/ws/widget/{session_id}")
async def widget_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for widget chat (anonymous users with customer OTP auth)."""
    await websocket.accept()
    settings = get_settings()
    cm = await get_connection_manager()

    # Extract source group from query params (?sg=uuid)
    source_group_id = websocket.query_params.get("sg")

    try:
        deps = await _create_chat_dependencies(settings)

        rate_limiter = RateLimiter(deps["redis_client"])
        client_ip = websocket.client.host if websocket.client else "unknown"

        # Check blacklist
        from app.services.blacklist_service import BlacklistService
        blacklist = BlacklistService(deps["redis_client"])
        if await blacklist.is_blacklisted(ip=client_ip, visitor_id=session_id):
            await websocket.send_json({"type": "error", "message": "Erisim engellendi"})
            await websocket.close(code=4003)
            return

        conversation_id = None

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg.get("type") == "typing" and conversation_id:
                await cm.send_to_agent(conversation_id, {"type": "typing", "sender": "customer"})
                continue

            if msg.get("type") != "message":
                continue

            # Rate limit check
            is_limited, retry_after = await rate_limiter.check_widget_limit(client_ip)
            if is_limited:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Cok fazla mesaj gonderdiniz. {retry_after} saniye bekleyin.",
                })
                continue

            content = msg.get("content", "").strip()
            attachments = msg.get("attachments")  # [{url, filename, type, ...}]
            if not content and not attachments:
                continue

            conversation_id = msg.get("conversation_id") or conversation_id

            async with async_session() as db:
                # Check conversation mode for human routing
                conv_mode = "ai"
                if conversation_id:
                    conv_result = await db.execute(
                        select(Conversation).where(
                            Conversation.id == _uuid.UUID(conversation_id)
                        )
                    )
                    conv = conv_result.scalar_one_or_none()
                    if conv:
                        conv_mode = conv.mode or "ai"
                        # Register widget connection
                        await cm.register_widget(conversation_id, websocket)

                if conv_mode == "human":
                    # Route to human agent — save message and forward
                    user_msg = Message(
                        conversation_id=_uuid.UUID(conversation_id),
                        role="user",
                        content=content or "",
                        sender_type="user",
                        attachments=attachments,
                    )
                    db.add(user_msg)
                    await db.commit()

                    await cm.send_to_agent(conversation_id, {
                        "type": "customer_message",
                        "content": content or "",
                        "conversation_id": conversation_id,
                        "message_id": str(user_msg.id),
                        "attachments": attachments,
                    })

                    # Acknowledge to widget so it resets typing indicator & input
                    await websocket.send_json({
                        "type": "human_ack",
                        "message_id": str(user_msg.id),
                        "conversation_id": conversation_id,
                    })
                else:
                    # Normal AI flow
                    chat = ChatService(
                        db=db,
                        rag_engine=deps["rag_engine"],
                        llm_service=deps["llm_service"],
                        odoo_service=deps["odoo_service"],
                        intent_classifier=deps["classifier"],
                        flow_manager=deps["flow_manager"],
                        customer_session=deps["customer_session"],
                        otp_service=deps["otp_service"],
                    )

                    async for chunk in chat.process_message_stream(
                        user_message=content,
                        conversation_id=conversation_id,
                        visitor_id=session_id,
                        channel="widget",
                        source_group_id=source_group_id,
                    ):
                        await websocket.send_json(chunk)
                        # Track conversation_id from stream_end
                        if chunk.get("type") == "stream_end" and chunk.get("conversation_id"):
                            conversation_id = chunk["conversation_id"]
                            await cm.register_widget(conversation_id, websocket)

                    await db.commit()

    except WebSocketDisconnect:
        logger.info("Widget WebSocket disconnected: %s", session_id)
        if conversation_id:
            await cm.unregister_widget(conversation_id)
    except Exception as e:
        logger.error("Widget WebSocket error: %s", e)
        if conversation_id:
            await cm.unregister_widget(conversation_id)
        try:
            await websocket.send_json({"type": "error", "message": "Baglanti hatasi olustu"})
        except Exception:
            pass


@router.websocket("/ws/chat/{conversation_id}")
async def panel_websocket(websocket: WebSocket, conversation_id: str):
    """WebSocket endpoint for employee panel chat."""
    await websocket.accept()
    settings = get_settings()

    try:
        deps = await _create_chat_dependencies(settings)
        source_group_id = None

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg.get("type") != "message":
                continue

            content = msg.get("content", "").strip()
            if not content:
                continue

            user_id = msg.get("user_id")
            # Allow panel user to specify source group per message or once
            if msg.get("source_group_id"):
                source_group_id = msg["source_group_id"]

            async with async_session() as db:
                chat = ChatService(
                    db=db,
                    rag_engine=deps["rag_engine"],
                    llm_service=deps["llm_service"],
                    odoo_service=deps["odoo_service"],
                    intent_classifier=deps["classifier"],
                    flow_manager=deps["flow_manager"],
                    customer_session=deps["customer_session"],
                    otp_service=deps["otp_service"],
                )

                async for chunk in chat.process_message_stream(
                    user_message=content,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    channel="panel",
                    source_group_id=source_group_id,
                ):
                    await websocket.send_json(chunk)

                await db.commit()

    except WebSocketDisconnect:
        logger.info("Panel WebSocket disconnected: %s", conversation_id)
    except Exception as e:
        logger.error("Panel WebSocket error: %s", e)


async def _authenticate_ws_token(websocket: WebSocket) -> User | None:
    """Authenticate WebSocket connection via ?token= query parameter."""
    token = websocket.query_params.get("token")
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return None
    user_id = payload.get("sub")
    try:
        user_uuid = _uuid.UUID(user_id)
    except (ValueError, TypeError):
        return None
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_uuid))
        user = result.scalar_one_or_none()
        if user and user.is_active:
            return user
    return None


@router.websocket("/ws/live-support/{conversation_id}")
async def agent_conversation_websocket(websocket: WebSocket, conversation_id: str):
    """WebSocket for an agent to chat with a customer in real-time."""
    # Authenticate
    user = await _authenticate_ws_token(websocket)
    if not user:
        await websocket.close(code=4001, reason="Yetkilendirme hatasi")
        return

    await websocket.accept()
    cm = await get_connection_manager()

    try:
        # Verify conversation is assigned to this agent
        async with async_session() as db:
            result = await db.execute(
                select(Conversation).where(
                    Conversation.id == _uuid.UUID(conversation_id)
                )
            )
            conv = result.scalar_one_or_none()
            if not conv or conv.mode != "human" or conv.assigned_agent_id != user.id:
                await websocket.send_json({
                    "type": "error",
                    "message": "Bu sohbeti devralmaniz gerekiyor",
                })
                await websocket.close()
                return

            # Preserve channel info for message routing
            conv_channel = conv.channel or "widget"
            conv_metadata = conv.metadata_ or {}

            # Load conversation history
            msg_result = await db.execute(
                select(Message)
                .where(Message.conversation_id == conv.id)
                .order_by(Message.created_at.asc())
            )
            messages = msg_result.scalars().all()
            await websocket.send_json({
                "type": "history",
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
                "conversation_id": conversation_id,
                "visitor_id": conv.visitor_id,
                "channel": conv_channel,
                "tags": conv.tags or [],
            })

        await cm.register_agent(conversation_id, websocket)

        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)

            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg.get("type") == "typing":
                await cm.send_to_widget(conversation_id, {"type": "typing", "sender": "agent"})
                continue

            if msg.get("type") != "message":
                continue

            content = msg.get("content", "").strip()
            attachments = msg.get("attachments")
            if not content and not attachments:
                continue

            # Save agent message to DB
            async with async_session() as db:
                agent_msg = Message(
                    conversation_id=_uuid.UUID(conversation_id),
                    role="assistant",
                    content=content or "",
                    sender_type="human",
                    agent_id=user.id,
                    attachments=attachments,
                )
                db.add(agent_msg)
                await db.commit()

                # Forward to customer: social channel via Meta API, widget via WebSocket
                from app.services.meta_sender import SOCIAL_CHANNELS
                if conv_channel in SOCIAL_CHANNELS:
                    from app.services.meta_sender import get_meta_sender, get_social_recipient
                    _conv_res = await db.execute(
                        select(Conversation).where(
                            Conversation.id == _uuid.UUID(conversation_id)
                        )
                    )
                    _conv = _conv_res.scalar_one_or_none()
                    if _conv:
                        recipient = get_social_recipient(_conv)
                        if recipient:
                            sender = get_meta_sender()
                            await sender.send_message(conv_channel, recipient, content or "")
                else:
                    await cm.send_to_widget(conversation_id, {
                        "type": "stream_start",
                        "message_id": str(agent_msg.id),
                    })
                    await cm.send_to_widget(conversation_id, {
                        "type": "stream_chunk",
                        "content": content or "",
                        "message_id": str(agent_msg.id),
                        "attachments": attachments,
                    })
                    await cm.send_to_widget(conversation_id, {
                        "type": "stream_end",
                        "message_id": str(agent_msg.id),
                        "conversation_id": conversation_id,
                        "sources": [],
                        "intent": "human_response",
                    })

    except WebSocketDisconnect:
        logger.info("Agent WS disconnected: %s (conv: %s)", user.email, conversation_id)
    except Exception as e:
        logger.error("Agent WS error: %s", e)
    finally:
        await cm.unregister_agent(conversation_id)


@router.websocket("/ws/live-support/notifications")
async def agent_notifications_websocket(websocket: WebSocket):
    """WebSocket for agents to receive real-time queue notifications."""
    user = await _authenticate_ws_token(websocket)
    if not user:
        await websocket.close(code=4001, reason="Yetkilendirme hatasi")
        return

    await websocket.accept()
    cm = await get_connection_manager()
    await cm.register_notification_listener(websocket)

    try:
        # Send current queue count on connect
        count = await cm.get_queue_count()
        await websocket.send_json({
            "type": "queue_update",
            "event": "connected",
            "waiting_count": count,
        })

        # Keep alive — just handle pings
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

    except WebSocketDisconnect:
        logger.info("Agent notification WS disconnected: %s", user.email)
    except Exception as e:
        logger.error("Agent notification WS error: %s", e)
    finally:
        await cm.unregister_notification_listener(websocket)
