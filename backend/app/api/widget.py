import uuid
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, Request
from qdrant_client import AsyncQdrantClient

from app.config import Settings, get_settings
from app.core.exceptions import RateLimitError
from app.core.rate_limiter import RateLimiter
from app.db.database import get_db
from app.dependencies import get_qdrant, get_rate_limiter, get_redis, get_visitor_id
from app.schemas.chat import ChatMessageRequest
from app.services.cache_service import CacheService
from app.services.chat_service import ChatService
from app.services.conversation_flow import FlowManager
from app.services.customer_session_service import CustomerSessionService
from app.services.flows.address_flow import AddressFlowHandler
from app.services.flows.cancel_order_flow import CancelOrderFlowHandler
from app.services.flows.order_flow import OrderFlowHandler
from app.services.flows.otp_flow import OTPFlowHandler
from app.services.flows.quotation_flow import QuotationFlowHandler
from app.services.flows.ticket_flow import TicketFlowHandler
from app.services.intent_classifier import IntentClassifier
from app.services.llm_service import LLMService
from app.services.odoo_service import OdooService, create_odoo_adapter
from app.services.otp_service import OTPService
from app.services.rag_engine import RAGEngine
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/widget", tags=["widget"])


@router.post("/init")
async def widget_init(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Initialize widget session and return config."""
    origin = request.headers.get("origin", "")
    allowed = settings.widget_allowed_domains.split(",") if settings.widget_allowed_domains else []

    # In development, allow all origins
    if settings.debug or not allowed:
        pass
    else:
        origin_domain = origin.replace("https://", "").replace("http://", "").split("/")[0]
        if origin_domain not in allowed:
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Domain izni yok")

    visitor_id = str(uuid.uuid4())

    # Pass through source_group_id and lang if provided
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    source_group_id = body.get("source_group_id") or request.query_params.get("source_group_id")
    lang = body.get("lang", "tr")

    # Multi-language support
    WIDGET_TEXTS = {
        "tr": {
            "welcome_message": "Merhaba! Ben ID Fine AI asistanıyım. Size nasıl yardımcı olabilirim?",
            "placeholder": "Mesajınızı yazın...",
            "connecting": "Bağlanıyor...",
            "agent_joined": "Bir temsilci sohbete katıldı.",
            "rate_prompt": "Hizmetimizi değerlendirin",
        },
        "en": {
            "welcome_message": "Hello! I'm ID Fine AI assistant. How can I help you?",
            "placeholder": "Type your message...",
            "connecting": "Connecting...",
            "agent_joined": "An agent has joined the chat.",
            "rate_prompt": "Rate our service",
        },
    }
    texts = WIDGET_TEXTS.get(lang, WIDGET_TEXTS["tr"])

    return {
        "visitor_id": visitor_id,
        "source_group_id": source_group_id,
        "lang": lang,
        "config": {
            "welcome_message": texts["welcome_message"],
            "placeholder": texts["placeholder"],
            "brand_name": "ID Fine",
            "brand_color": "#231f20",
            "position": "bottom-right",
            "proactive_message": None,
            "proactive_delay": 0,
            "announcement": None,
        },
        "texts": texts,
    }


@router.get("/config")
async def widget_config(settings: Annotated[Settings, Depends(get_settings)]):
    """Get widget configuration."""
    return {
        "brand_name": "ID Fine",
        "brand_color": "#231f20",
        "welcome_message": "Merhaba! Ben ID Fine AI asistanıyım. Size nasıl yardımcı olabilirim?",
        "placeholder": "Mesajınızı yazın...",
    }


@router.post("/message")
async def widget_message(
    body: ChatMessageRequest,
    request: Request,
    visitor_id: Annotated[str, Depends(get_visitor_id)],
    rate_limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    db: Annotated[AsyncSession, Depends(get_db)],
    qdrant: Annotated[AsyncQdrantClient, Depends(get_qdrant)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """REST fallback for widget messages (non-streaming)."""
    client_ip = request.client.host if request.client else "unknown"

    # Check blacklist
    from app.services.blacklist_service import BlacklistService
    blacklist = BlacklistService(redis_client)
    if await blacklist.is_blacklisted(ip=client_ip, visitor_id=visitor_id):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Erisim engellendi")

    is_limited, retry_after = await rate_limiter.check_widget_limit(client_ip)
    if is_limited:
        raise RateLimitError(retry_after or 60)

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
    if odoo_adapter:
        otp_flow = OTPFlowHandler(otp_service, customer_session, odoo_adapter)
        flow_manager.register_handler(otp_flow)
    if odoo_service:
        flow_manager.register_handler(TicketFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(CancelOrderFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(AddressFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(OrderFlowHandler(odoo_service, customer_session))
        flow_manager.register_handler(QuotationFlowHandler(odoo_service, customer_session))

    chat = ChatService(
        db=db,
        rag_engine=rag_engine,
        llm_service=llm_service,
        odoo_service=odoo_service,
        intent_classifier=classifier,
        flow_manager=flow_manager,
        customer_session=customer_session,
        otp_service=otp_service,
    )

    result = await chat.process_message(
        user_message=body.content,
        conversation_id=body.conversation_id,
        visitor_id=visitor_id,
        channel="widget",
        source_group_id=body.source_group_id,
    )

    return result


@router.post("/rate/{conversation_id}")
async def rate_conversation(
    conversation_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Submit a CSAT rating for a conversation (public - widget users)."""
    from sqlalchemy import select
    from app.models.conversation import Conversation
    from pydantic import BaseModel

    body = await request.json()
    rating = body.get("rating")
    comment = body.get("comment", "")

    if not rating or rating not in (1, 2, 3, 4, 5):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Rating must be 1-5")

    result = await db.execute(
        select(Conversation).where(Conversation.id == uuid.UUID(conversation_id))
    )
    conv = result.scalar_one_or_none()
    if not conv:
        from app.core.exceptions import NotFoundError
        raise NotFoundError("Sohbet bulunamadi")

    conv.rating = rating
    conv.rating_comment = comment[:500] if comment else None
    await db.commit()

    return {"status": "ok", "conversation_id": conversation_id, "rating": rating}
