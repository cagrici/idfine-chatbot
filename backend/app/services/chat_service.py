import asyncio
import logging
import random
import re
import uuid
from typing import AsyncGenerator, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.conversation import Conversation, Message
from app.models.source_group import SourceGroup
from app.services.conversation_flow import FlowManager, FlowType
from app.services.customer_session_service import CustomerSessionService
from app.services.intent_classifier import Intent, IntentClassifier
from app.services.llm_service import LLMService
from app.services.odoo_service import OdooService
from app.services.otp_service import OTPService
from app.services.product_db_service import ProductDBService
from app.services.rag_engine import RAGEngine

logger = logging.getLogger(__name__)
settings = get_settings()

# Pre-defined greeting responses per language (avoids LLM call entirely)
_GREETING_RESPONSES = {
    "tr": [
        "Merhaba! Ben ID Fine AI asistanıyım. Size ürünlerimiz, fiyatlarımız veya stok durumu hakkında yardımcı olabilirim. Nasıl yardımcı olabilirim?",
        "Merhaba! ID Fine'a hoş geldiniz. Ürünler, fiyatlar veya sipariş hakkında sorularınızı yanıtlayabilirim. Size nasıl yardımcı olabilirim?",
        "Hoş geldiniz! Ben ID Fine müşteri destek asistanıyım. Ürün bilgisi, fiyat veya stok sorgulaması için buradayım. Buyurun, nasıl yardımcı olabilirim?",
    ],
    "en": [
        "Hello! I'm the ID Fine AI assistant. I can help you with our products, prices, or stock availability. How can I assist you?",
        "Welcome to ID Fine! I can answer your questions about our porcelain products, pricing, and orders. How can I help?",
        "Hi there! I'm the ID Fine customer support assistant. I'm here for product info, pricing, or stock inquiries. What can I help you with?",
    ],
}
_FAREWELL_RESPONSES = {
    "tr": [
        "Rica ederim! Başka bir sorunuz olursa her zaman buradayım. İyi günler!",
        "Yardımcı olabildiysem ne mutlu! İyi günler dilerim.",
        "Her zaman buradayım. İyi günler!",
    ],
    "en": [
        "You're welcome! If you have any other questions, I'm always here. Have a great day!",
        "Glad I could help! Have a wonderful day.",
        "I'm always here if you need anything. Take care!",
    ],
}

# Turkish state name translations for display
_ORDER_STATE_TR = {
    "draft": "Taslak", "sent": "Gonderildi", "sale": "Onaylandi",
    "done": "Tamamlandi", "cancel": "Iptal",
}
_INVOICE_STATE_TR = {
    "draft": "Taslak", "posted": "Kesildi", "cancel": "Iptal",
}
_PAYMENT_STATE_TR = {
    "paid": "Odendi", "not_paid": "Odenmedi", "partial": "Kismi Odendi",
    "in_payment": "Odeme Surecinde",
}
_DELIVERY_STATE_TR = {
    "draft": "Taslak", "waiting": "Beklemede", "confirmed": "Onaylandi",
    "assigned": "Hazir", "done": "Teslim Edildi", "cancel": "Iptal",
}

# Maps each Intent to a chatbot_features key for granular toggle control.
# If an intent is not listed here, it is always allowed.
INTENT_FEATURE_MAP: dict[Intent, str] = {
    Intent.ORDER_HISTORY: "order_view",
    Intent.ORDER_DETAIL: "order_view",
    Intent.ORDER_STATUS: "order_view",
    Intent.ORDER_CREATE: "order_create",
    Intent.QUOTE_REQUEST: "order_create",
    Intent.ORDER_CANCEL: "order_cancel",
    Intent.INVOICE_LIST: "invoice_view",
    Intent.INVOICE_DETAIL: "invoice_view",
    Intent.INVOICE_DOWNLOAD: "invoice_download",
    Intent.PAYMENT_STATUS: "payment_view",
    Intent.PAYMENT_HISTORY: "payment_view",
    Intent.DELIVERY_TRACKING: "delivery_tracking",
    Intent.PROFILE_VIEW: "profile_view",
    Intent.PROFILE_UPDATE: "profile_update",
    Intent.ADDRESS_UPDATE: "profile_update",
    Intent.SUPPORT_TICKET_CREATE: "support_ticket",
    Intent.SUPPORT_TICKET_LIST: "support_ticket",
    Intent.COMPLAINT: "complaint",
    Intent.SPENDING_REPORT: "spending_report",
    Intent.CATALOG_REQUEST: "catalog_request",
    Intent.FIND_DEALER: "find_dealer",
}

_FEATURE_DISABLED_MSG = "Bu özellik şu anda devre dışıdır. Lütfen başka bir konuda yardımcı olabileceğim bir soru sorun."

_PRICE_GUEST_MSG = (
    "Ürün fiyatları adet, kullanım alanı ve ürün tipine göre değişiklik göstermektedir. "
    "Bu nedenle bireysel fiyat paylaşımı yapılmamaktadır.\n\n"
    "Fiyat bilgisi almak için aşağıdaki seçeneklerden birini kullanabilirsiniz:"
)
_PRICE_GUEST_ACTIONS = [
    {"label": "Bayi Bul", "message": "Bayi bulmak istiyorum"},
    {"label": "Talep Bırak", "message": "Fiyat teklifi almak istiyorum"},
]


class ChatService:
    """Orchestrator: receives user messages, routes to RAG/ProductDB/Odoo, calls LLM."""

    def __init__(
        self,
        db: AsyncSession,
        rag_engine: RAGEngine,
        llm_service: LLMService,
        odoo_service: OdooService | None,
        intent_classifier: IntentClassifier,
        flow_manager: FlowManager | None = None,
        customer_session: CustomerSessionService | None = None,
        otp_service: OTPService | None = None,
    ):
        self.db = db
        self.rag = rag_engine
        self.llm = llm_service
        self.odoo = odoo_service
        self.classifier = intent_classifier
        self.product_db = ProductDBService(db)
        self.flow_manager = flow_manager
        self.customer_session = customer_session
        self.otp_service = otp_service

    async def get_or_create_conversation(
        self,
        conversation_id: str | None = None,
        user_id: str | None = None,
        visitor_id: str | None = None,
        channel: str = "widget",
        source_group_id: str | None = None,
    ) -> Conversation:
        if conversation_id:
            result = await self.db.execute(
                select(Conversation).where(
                    Conversation.id == uuid.UUID(conversation_id)
                )
            )
            conv = result.scalar_one_or_none()
            if conv:
                return conv

        conv = Conversation(
            user_id=uuid.UUID(user_id) if user_id else None,
            visitor_id=visitor_id,
            channel=channel,
            source_group_id=uuid.UUID(source_group_id) if source_group_id else None,
        )
        self.db.add(conv)
        await self.db.flush()
        return conv

    async def get_conversation_history(
        self, conversation_id: uuid.UUID, limit: int = 10
    ) -> list[dict]:
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        messages = result.scalars().all()
        messages.reverse()

        return [
            {"role": m.role, "content": m.content}
            for m in messages
        ]

    async def _load_source_group_permissions(self, source_group_id: str | None) -> dict:
        """Load data_permissions for a source group. Returns permissive defaults if None."""
        default_perms = {"rag_enabled": True, "product_db_enabled": True, "odoo_enabled": True, "odoo_scopes": []}
        if not source_group_id:
            return default_perms
        try:
            result = await self.db.execute(
                select(SourceGroup).where(SourceGroup.id == uuid.UUID(source_group_id))
            )
            sg = result.scalar_one_or_none()
            if sg and sg.data_permissions:
                return sg.data_permissions
        except Exception:
            pass
        return default_perms

    @staticmethod
    def _is_feature_enabled(perms: dict, intent: Intent) -> bool:
        """Check if a chatbot feature is enabled for the given intent."""
        feature_key = INTENT_FEATURE_MAP.get(intent)
        if not feature_key:
            return True
        features = perms.get("chatbot_features", {})
        return features.get(feature_key, True)

    async def process_message(
        self,
        user_message: str,
        conversation_id: str | None = None,
        user_id: str | None = None,
        visitor_id: str | None = None,
        channel: str = "widget",
        source_group_id: str | None = None,
    ) -> dict:
        """Process a message and return the full response (non-streaming)."""
        conv = await self.get_or_create_conversation(
            conversation_id, user_id, visitor_id, channel, source_group_id
        )

        # Save user message
        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=user_message,
        )
        self.db.add(user_msg)
        await self.db.flush()

        conv_id_str = str(conv.id)

        # Check active flow
        if self.flow_manager:
            flow_result = await self.flow_manager.process_step(
                conv_id_str, user_message, visitor_id or ""
            )
            if flow_result:
                # If flow was cancelled with empty message, fall through to normal processing
                if not (flow_result.flow_cancelled and not flow_result.message):
                    return await self._save_and_return(
                        conv, flow_result.message, Intent.CUSTOMER_AUTH, [], None
                    )

        # Classify intent
        intent = await self.classifier.classify(user_message)
        user_msg.intent = intent.value

        # Fast-path: greeting/farewell
        if self.classifier.is_greeting:
            lang = self.classifier.greeting_lang
            text = random.choice(_GREETING_RESPONSES.get(lang, _GREETING_RESPONSES["tr"]))
            lower = user_message.strip().lower()
            if any(w in lower for w in ("teşekkür", "tesekkur", "sağ ol", "sag ol", "hoşça kal", "hosca kal", "görüşürüz", "gorusuruz", "güle güle", "gule gule", "thanks", "thank you", "bye", "goodbye", "see you", "take care")):
                text = random.choice(_FAREWELL_RESPONSES.get(lang, _FAREWELL_RESPONSES["tr"]))
            return await self._save_and_return(conv, text, intent, [], None)

        # Customer auth / logout
        if intent == Intent.CUSTOMER_AUTH:
            text = await self._start_otp_flow(conv_id_str, visitor_id, intent.value)
            return await self._save_and_return(conv, text, intent, [], None)

        if intent == Intent.CUSTOMER_LOGOUT:
            text = await self._handle_logout(visitor_id)
            return await self._save_and_return(conv, text, intent, [], None)

        # Feature toggle check for non-auth intents
        perms = await self._load_source_group_permissions(source_group_id)

        # Price inquiry gate for guest users (stock queries are allowed with Var/Yok only)
        if intent == Intent.PRICE_INQUIRY:
            has_session = False
            if self.customer_session and visitor_id:
                session = await self.customer_session.get_session(visitor_id)
                has_session = session is not None
            if not has_session:
                result = await self._save_and_return(conv, _PRICE_GUEST_MSG, intent, [], None)
                result["actions"] = _PRICE_GUEST_ACTIONS
                return result

        if intent == Intent.CATALOG_REQUEST:
            if not self._is_feature_enabled(perms, intent):
                return await self._save_and_return(conv, _FEATURE_DISABLED_MSG, intent, [], None)
            text = self._build_catalog_response(user_message)
            return await self._save_and_return(conv, text, intent, [], None)

        # Complaint flow (no auth required)
        if intent == Intent.COMPLAINT:
            if not self._is_feature_enabled(perms, intent):
                return await self._save_and_return(conv, _FEATURE_DISABLED_MSG, intent, [], None)
            flow_msg = await self._maybe_start_flow(intent, conv_id_str, visitor_id)
            if flow_msg:
                return await self._save_and_return(conv, flow_msg, intent, [], None)

        # Find Dealer flow (no auth required)
        if intent == Intent.FIND_DEALER:
            if not self._is_feature_enabled(perms, intent):
                return await self._save_and_return(conv, _FEATURE_DISABLED_MSG, intent, [], None)
            flow_msg = await self._maybe_start_flow(intent, conv_id_str, visitor_id)
            if flow_msg:
                return await self._save_and_return(conv, flow_msg, intent, [], None)

        # Customer auth gate
        if intent.requires_customer_auth:
            # Check if source group allows Odoo access
            if not perms.get("odoo_enabled", True):
                text = "Bu hizmet bu kanal üzerinden kullanılamamaktadır. Lütfen müşteri portalınızı kullanın."
                return await self._save_and_return(conv, text, intent, [], None)

            # Granular feature toggle check
            if not self._is_feature_enabled(perms, intent):
                return await self._save_and_return(conv, _FEATURE_DISABLED_MSG, intent, [], None)

            session = None
            if self.customer_session and visitor_id:
                session = await self.customer_session.get_session(visitor_id)

            if not session:
                text = await self._start_otp_flow(conv_id_str, visitor_id, intent.value)
                return await self._save_and_return(conv, text, intent, [], None)

            await self.customer_session.extend_session(visitor_id)

            # Check if intent requires a multi-step flow
            flow_msg = await self._maybe_start_flow(intent, conv_id_str, visitor_id)
            if flow_msg:
                return await self._save_and_return(conv, flow_msg, intent, [], None)

            customer_data = await self._handle_customer_intent(
                intent, user_message, session.partner_id, visitor_id
            )
            if customer_data:
                history = await self.get_conversation_history(conv.id)
                response_text = await self.llm.generate(
                    user_message=user_message,
                    context="",
                    conversation_history=history,
                    product_data="",
                    customer_data=customer_data,
                )
                return await self._save_and_return(conv, response_text, intent, [], None)

        # Handle out of scope
        if intent == Intent.OUT_OF_SCOPE:
            response_text = (
                "Uzgunum, bu konu hakkinda size yardimci olamam. "
                "Ben sadece ID Fine urunleri ve hizmetleri hakkinda "
                "bilgi verebilirim."
            )
            return await self._save_and_return(conv, response_text, intent, [], None)

        # Standard flow
        context, sources, product_context = await self._gather_context(
            user_message, intent, source_group_id, visitor_id
        )
        history = await self.get_conversation_history(conv.id)

        response_text = await self.llm.generate(
            user_message=user_message,
            context=context,
            conversation_history=history,
            product_data=product_context,
        )

        return await self._save_and_return(conv, response_text, intent, sources, None)

    async def process_message_stream(
        self,
        user_message: str,
        conversation_id: str | None = None,
        user_id: str | None = None,
        visitor_id: str | None = None,
        channel: str = "widget",
        source_group_id: str | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Process a message and yield streaming chunks."""
        conv = await self.get_or_create_conversation(
            conversation_id, user_id, visitor_id, channel, source_group_id
        )

        # Save user message
        user_msg = Message(
            conversation_id=conv.id,
            role="user",
            content=user_message,
        )
        self.db.add(user_msg)
        await self.db.flush()

        message_id = str(uuid.uuid4())
        conv_id_str = str(conv.id)

        # --- Step 1: Check for active multi-step flow ---
        if self.flow_manager:
            flow_result = await self.flow_manager.process_step(
                conv_id_str, user_message, visitor_id or ""
            )
            if flow_result:
                # If flow was cancelled with empty message, fall through to normal processing
                if flow_result.flow_cancelled and not flow_result.message:
                    pass  # Continue to Step 2 (intent classification)
                else:
                    intent = Intent.CUSTOMER_AUTH
                    user_msg.intent = intent.value

                    yield {"type": "stream_start", "message_id": message_id}
                    yield {"type": "stream_chunk", "content": flow_result.message, "message_id": message_id}
                    yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                    await self._save_assistant_message(conv.id, flow_result.message, intent, [], None)

                    # If OTP flow completed successfully, re-process original intent
                    if flow_result.flow_completed and flow_result.data.get("original_intent"):
                        original_intent_str = flow_result.data["original_intent"]
                        try:
                            original_intent = Intent(original_intent_str)
                            # If original intent has a multi-step flow (e.g. QUOTE_REQUEST →
                            # QUOTATION_CREATE), start that flow now instead of asking the LLM
                            flow_msg = await self._maybe_start_flow(original_intent, conv_id_str, visitor_id)
                            if flow_msg:
                                yield {"type": "stream_start", "message_id": message_id}
                                yield {"type": "stream_chunk", "content": flow_msg, "message_id": message_id}
                                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": original_intent.value}
                                await self._save_assistant_message(conv.id, flow_msg, original_intent, [], None)
                            else:
                                customer_data = await self._handle_customer_intent(
                                    original_intent, user_message, flow_result.data.get("partner_id"), visitor_id
                                )
                                if customer_data:
                                    history = await self.get_conversation_history(conv.id)
                                    async for chunk in self._stream_llm_response(
                                        user_message, "", [], "", customer_data,
                                        history, message_id, conv, original_intent
                                    ):
                                        yield chunk
                        except (ValueError, Exception) as e:
                            logger.error("Error re-processing original intent: %s", e)
                    return

        # --- Step 2: Classify intent ---
        intent = await self.classifier.classify(user_message)
        user_msg.intent = intent.value

        # Fast-path: greeting/farewell
        if self.classifier.is_greeting:
            lang = self.classifier.greeting_lang
            text = random.choice(_GREETING_RESPONSES.get(lang, _GREETING_RESPONSES["tr"]))
            lower = user_message.strip().lower()
            if any(w in lower for w in ("teşekkür", "tesekkur", "sağ ol", "sag ol", "hoşça kal", "hosca kal", "görüşürüz", "gorusuruz", "güle güle", "gule gule", "thanks", "thank you", "bye", "goodbye", "see you", "take care")):
                text = random.choice(_FAREWELL_RESPONSES.get(lang, _FAREWELL_RESPONSES["tr"]))
            yield {"type": "stream_start", "message_id": message_id}
            yield {"type": "stream_chunk", "content": text, "message_id": message_id}
            yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
            await self._save_assistant_message(conv.id, text, intent, [], None)
            return

        # --- Step 3: Customer auth intent ---
        if intent == Intent.CUSTOMER_AUTH:
            text = await self._start_otp_flow(conv_id_str, visitor_id, intent.value)
            yield {"type": "stream_start", "message_id": message_id}
            yield {"type": "stream_chunk", "content": text, "message_id": message_id}
            yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
            await self._save_assistant_message(conv.id, text, intent, [], None)
            return

        # --- Step 4: Customer logout ---
        if intent == Intent.CUSTOMER_LOGOUT:
            text = await self._handle_logout(visitor_id)
            yield {"type": "stream_start", "message_id": message_id}
            yield {"type": "stream_chunk", "content": text, "message_id": message_id}
            yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
            await self._save_assistant_message(conv.id, text, intent, [], None)
            return

        # Feature toggle check (loaded once for streaming path)
        perms = await self._load_source_group_permissions(source_group_id)

        # --- Step 4.4: Price inquiry gate for guest users (stock allowed with Var/Yok only) ---
        if intent == Intent.PRICE_INQUIRY:
            has_session = False
            if self.customer_session and visitor_id:
                session = await self.customer_session.get_session(visitor_id)
                has_session = session is not None
            if not has_session:
                text = _PRICE_GUEST_MSG
                actions = _PRICE_GUEST_ACTIONS
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": text, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value, "actions": actions}
                await self._save_assistant_message(conv.id, text, intent, [], None)
                return

        # --- Step 4.5: Catalog request (direct response, no LLM) ---
        if intent == Intent.CATALOG_REQUEST:
            if not self._is_feature_enabled(perms, intent):
                text = _FEATURE_DISABLED_MSG
            else:
                text = self._build_catalog_response(user_message)
            yield {"type": "stream_start", "message_id": message_id}
            yield {"type": "stream_chunk", "content": text, "message_id": message_id}
            yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
            await self._save_assistant_message(conv.id, text, intent, [], None)
            return

        # --- Step 4.6: Complaint flow (no auth required) ---
        if intent == Intent.COMPLAINT:
            if not self._is_feature_enabled(perms, intent):
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": _FEATURE_DISABLED_MSG, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                await self._save_assistant_message(conv.id, _FEATURE_DISABLED_MSG, intent, [], None)
                return
            flow_msg = await self._maybe_start_flow(intent, conv_id_str, visitor_id)
            if flow_msg:
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": flow_msg, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                await self._save_assistant_message(conv.id, flow_msg, intent, [], None)
                return

        # --- Step 4.7: Find Dealer flow (no auth required) ---
        if intent == Intent.FIND_DEALER:
            if not self._is_feature_enabled(perms, intent):
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": _FEATURE_DISABLED_MSG, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                await self._save_assistant_message(conv.id, _FEATURE_DISABLED_MSG, intent, [], None)
                return
            flow_msg = await self._maybe_start_flow(intent, conv_id_str, visitor_id)
            if flow_msg:
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": flow_msg, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                await self._save_assistant_message(conv.id, flow_msg, intent, [], None)
                return

        # --- Step 5: Customer auth gate for restricted intents ---
        if intent.requires_customer_auth:
            # Check if source group allows Odoo access
            if not perms.get("odoo_enabled", True):
                text = "Bu hizmet bu kanal üzerinden kullanılamamaktadır. Lütfen müşteri portalınızı kullanın."
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": text, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                await self._save_assistant_message(conv.id, text, intent, [], None)
                return

            # Granular feature toggle check
            if not self._is_feature_enabled(perms, intent):
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": _FEATURE_DISABLED_MSG, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                await self._save_assistant_message(conv.id, _FEATURE_DISABLED_MSG, intent, [], None)
                return

            session = None
            if self.customer_session and visitor_id:
                session = await self.customer_session.get_session(visitor_id)

            if not session:
                # Start OTP flow
                text = await self._start_otp_flow(conv_id_str, visitor_id, intent.value)
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": text, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                await self._save_assistant_message(conv.id, text, intent, [], None)
                return

            # Extend session TTL on activity
            await self.customer_session.extend_session(visitor_id)

            # Check if intent requires a multi-step flow
            flow_msg = await self._maybe_start_flow(intent, conv_id_str, visitor_id)
            if flow_msg:
                yield {"type": "stream_start", "message_id": message_id}
                yield {"type": "stream_chunk", "content": flow_msg, "message_id": message_id}
                yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
                await self._save_assistant_message(conv.id, flow_msg, intent, [], None)
                return

            # Handle customer intent with Odoo data
            customer_data = await self._handle_customer_intent(
                intent, user_message, session.partner_id, visitor_id
            )

            if customer_data:
                history = await self.get_conversation_history(conv.id)
                async for chunk in self._stream_llm_response(
                    user_message, "", [], "", customer_data,
                    history, message_id, conv, intent
                ):
                    yield chunk
                return

        # --- Step 6: Handle out of scope ---
        if intent == Intent.OUT_OF_SCOPE:
            text = (
                "Uzgunum, bu konu hakkinda size yardimci olamam. "
                "Ben sadece ID Fine urunleri ve hizmetleri hakkinda "
                "bilgi verebilirim."
            )
            yield {"type": "stream_start", "message_id": message_id}
            yield {"type": "stream_chunk", "content": text, "message_id": message_id}
            yield {"type": "stream_end", "message_id": message_id, "conversation_id": conv_id_str, "sources": [], "intent": intent.value}
            await self._save_assistant_message(conv.id, text, intent, [], None)
            return

        # --- Step 7: Standard flow (RAG + ProductDB + LLM) ---
        context, sources, product_context = await self._gather_context(
            user_message, intent, source_group_id, visitor_id
        )
        history = await self.get_conversation_history(conv.id)

        async for chunk in self._stream_llm_response(
            user_message, context, sources, product_context, "",
            history, message_id, conv, intent
        ):
            yield chunk

    async def _stream_llm_response(
        self,
        user_message: str,
        context: str,
        sources: list,
        product_context: str,
        customer_data: str,
        history: list[dict],
        message_id: str,
        conv: Conversation,
        intent: Intent,
    ) -> AsyncGenerator[dict, None]:
        """Stream LLM response with all context types."""
        conv_id_str = str(conv.id)

        yield {"type": "stream_start", "message_id": message_id}

        full_response = []
        async for chunk in self.llm.generate_stream(
            user_message=user_message,
            context=context,
            conversation_history=history,
            product_data=product_context,
            customer_data=customer_data,
        ):
            full_response.append(chunk)
            yield {"type": "stream_chunk", "content": chunk, "message_id": message_id}

        response_text = "".join(full_response)
        yield {
            "type": "stream_end",
            "message_id": message_id,
            "conversation_id": conv_id_str,
            "sources": sources,
            "intent": intent.value,
        }

        await self._save_assistant_message(
            conv.id, response_text, intent, sources, None
        )

    # --- Customer flow helpers ---

    async def _start_otp_flow(self, conv_id: str, visitor_id: str | None, original_intent: str) -> str:
        """Start OTP authentication flow."""
        if not self.flow_manager:
            return "Kimlik dogrulama sistemi su anda kullanilamamaktadir."

        await self.flow_manager.start_flow(
            conv_id,
            FlowType.OTP_AUTH,
            initial_data={"original_intent": original_intent},
        )
        return (
            "Bu bilgilere erismek icin kimliginizi dogrulamam gerekiyor.\n"
            "Lutfen ID Fine kayitlarimizda gecen **e-posta adresinizi** yazin."
        )

    async def _handle_logout(self, visitor_id: str | None) -> str:
        """Handle customer logout."""
        if self.customer_session and visitor_id:
            destroyed = await self.customer_session.destroy_session(visitor_id)
            if destroyed:
                return "Basariyla cikis yapildi. Tekrar ihtiyaciniz olursa kimlik dogrulama yapabilirsiniz."
        return "Zaten aktif bir oturum bulunmuyor."

    # Intent -> FlowType mapping for multi-step flows
    _FLOW_INTENTS: dict = {
        Intent.ORDER_CREATE: FlowType.ORDER_CREATE,
        Intent.ORDER_CANCEL: FlowType.ORDER_CANCEL,
        Intent.SUPPORT_TICKET_CREATE: FlowType.TICKET_CREATE,
        Intent.COMPLAINT: FlowType.COMPLAINT,
        Intent.FIND_DEALER: FlowType.FIND_DEALER,
        Intent.PROFILE_UPDATE: FlowType.ADDRESS_UPDATE,
        Intent.ADDRESS_UPDATE: FlowType.ADDRESS_UPDATE,
        Intent.QUOTE_REQUEST: FlowType.QUOTATION_CREATE,
    }

    async def _maybe_start_flow(
        self, intent: Intent, conv_id: str, visitor_id: str | None
    ) -> str | None:
        """If the intent maps to a multi-step flow, start it and return the intro message."""
        flow_type = self._FLOW_INTENTS.get(intent)
        if not flow_type or not self.flow_manager:
            return None

        prompt_messages = {
            FlowType.ORDER_CREATE: (
                "Siparis talebinizi almak istiyorum.\n"
                "Lutfen siparis etmek istediginiz urunleri ve miktarlari yazin.\n"
                "Ornegin: **ABC123 x 10** veya urun adlarini belirtin."
            ),
            FlowType.ORDER_CANCEL: (
                "Siparis iptal islemi icin yardimci olabilirim.\n"
                "Lutfen iptal etmek istediginiz siparis numarasini yazin. Ornegin: **S00123**"
            ),
            FlowType.TICKET_CREATE: (
                "Destek talebi olusturmak icin size yardimci olacagim.\n"
                "Lutfen talebiniz icin bir **konu basligi** yazin."
            ),
            FlowType.ADDRESS_UPDATE: (
                "Profil bilgilerinizi guncellemek icin yardimci olabilirim.\n"
                "Lutfen guncellemek istediginiz alani secin:\n"
                "- **telefon** - Sabit telefon\n"
                "- **mobil** - Cep telefonu\n"
                "- **email** - E-posta adresi\n"
                "- **adres** - Sokak/cadde adresi\n"
                "- **sehir** - Sehir\n"
                "- **posta kodu** - Posta kodu"
            ),
            FlowType.QUOTATION_CREATE: (
                "Fiyat teklifi talebi olusturmak icin yardimci olacagim.\n\n"
                "Lutfen teklif almak istediginiz **urun kodlarini ve miktarlari** asagidaki formatta girin "
                "(her urunu ayri satira):\n\n"
                "**urun\\_kodu, miktar**\n\n"
                "Ornek:\n"
                "20257-111030, 50\n"
                "20257-111031, 10"
            ),
            FlowType.COMPLAINT: (
                "Sikayetinizi almak icin size yardimci olacagim.\n"
                "Lutfen adinizi ve soyadinizi yaziniz."
            ),
            FlowType.FIND_DEALER: (
                "Bayi bulma islemini baslatiyorum. Lutfen bekleyiniz..."
            ),
        }

        await self.flow_manager.start_flow(conv_id, flow_type)

        # FIND_DEALER: auto-process first step to load cities immediately
        if flow_type == FlowType.FIND_DEALER:
            result = await self.flow_manager.process_step(conv_id, "", visitor_id or "")
            if result and result.message:
                return result.message
            return prompt_messages.get(flow_type, "Islem basladi. Lutfen bilgileri girin.")

        return prompt_messages.get(flow_type, "Islem basladi. Lutfen bilgileri girin.")

    async def _handle_customer_intent(
        self, intent: Intent, user_message: str, partner_id: int, visitor_id: str | None
    ) -> str:
        """Handle customer intents by querying Odoo and formatting as context text."""
        if not self.odoo:
            return "ERP sistemi baglantisi yapilamiyor. Lutfen daha sonra tekrar deneyin."

        try:
            if intent == Intent.ORDER_HISTORY:
                return await self._format_orders(partner_id)
            elif intent == Intent.ORDER_DETAIL:
                return await self._format_order_detail(partner_id, user_message)
            elif intent in (Intent.INVOICE_LIST, Intent.INVOICE_DETAIL):
                return await self._format_invoices(partner_id)
            elif intent == Intent.INVOICE_DOWNLOAD:
                return await self._format_invoice_download(partner_id, user_message)
            elif intent in (Intent.PAYMENT_STATUS, Intent.PAYMENT_HISTORY):
                return await self._format_payments(partner_id)
            elif intent == Intent.DELIVERY_TRACKING:
                return await self._format_deliveries(partner_id)
            elif intent == Intent.PROFILE_VIEW:
                return await self._format_profile(partner_id)
            elif intent == Intent.SUPPORT_TICKET_LIST:
                return await self._format_tickets(partner_id)
            elif intent == Intent.SPENDING_REPORT:
                return await self._format_spending_report(partner_id)
            else:
                # Flow-based intents (ORDER_CREATE, QUOTE_REQUEST, etc.) should never reach here
                # because _maybe_start_flow() is always called before _handle_customer_intent().
                # Returning empty string prevents LLM from generating a fake response.
                logger.warning("_handle_customer_intent called for flow intent %s — returning empty", intent)
                return ""
        except Exception as e:
            logger.error("Customer intent error (%s): %s", intent, e)
            return "<musteri_bilgisi>Musteri verileri alinirken bir hata olustu.</musteri_bilgisi>"

    async def _format_orders(self, partner_id: int) -> str:
        orders = await self.odoo.get_partner_orders(partner_id, limit=15)
        if not orders:
            return "<musteri_verileri>Kayitli siparis bulunamadi.</musteri_verileri>"

        lines = ["Musteri Siparisleri:"]
        for o in orders:
            state_tr = _ORDER_STATE_TR.get(o.state, o.state)
            date = o.date_order[:10] if o.date_order else "-"
            lines.append(
                f"- {o.name} | Tarih: {date} | Durum: {state_tr} | "
                f"Tutar: {o.amount_total:,.2f} {o.currency}"
            )
        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _format_order_detail(self, partner_id: int, message: str) -> str:
        # Try to find order ID from message
        order_ref = self._extract_order_ref(message)
        if order_ref:
            orders = await self.odoo.get_partner_orders(partner_id, limit=100)
            match = next((o for o in orders if order_ref.upper() in o.name.upper()), None)
            if match:
                detail = await self.odoo.get_order_details(match.id, partner_id)
                if detail:
                    return self._format_order_detail_text(detail)

        # Fallback: show recent orders
        return await self._format_orders(partner_id)

    def _format_order_detail_text(self, detail) -> str:
        state_tr = _ORDER_STATE_TR.get(detail.state, detail.state)
        lines = [
            f"Siparis Detayi: {detail.name}",
            f"Durum: {state_tr}",
            f"Tarih: {detail.date_order[:10] if detail.date_order else '-'}",
            f"Ara Toplam: {detail.amount_untaxed:,.2f} {detail.currency}",
            f"KDV: {detail.amount_tax:,.2f} {detail.currency}",
            f"Toplam: {detail.amount_total:,.2f} {detail.currency}",
            "",
            "Kalemler:",
        ]
        for ln in detail.lines:
            lines.append(f"  - {ln.product_name}: {ln.quantity} x {ln.price_unit:,.2f} = {ln.price_subtotal:,.2f}")

        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _format_invoices(self, partner_id: int) -> str:
        invoices = await self.odoo.get_partner_invoices(partner_id, limit=15)
        if not invoices:
            return "<musteri_verileri>Kayitli fatura bulunamadi.</musteri_verileri>"

        lines = ["Musteri Faturalari:"]
        for inv in invoices:
            state_tr = _INVOICE_STATE_TR.get(inv.state, inv.state)
            pay_state = _PAYMENT_STATE_TR.get(inv.payment_state or "", inv.payment_state or "-")
            date = inv.date[:10] if inv.date else "-"
            due = inv.invoice_date_due[:10] if inv.invoice_date_due else "-"
            lines.append(
                f"- {inv.name} | Tarih: {date} | Vade: {due} | Durum: {state_tr} | "
                f"Odeme: {pay_state} | Tutar: {inv.amount_total:,.2f} | "
                f"Kalan: {inv.amount_residual:,.2f} {inv.currency}"
            )
        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _format_invoice_download(self, partner_id: int, message: str) -> str:
        invoices = await self.odoo.get_partner_invoices(partner_id, limit=10)
        if not invoices:
            return "<musteri_verileri>Indirilecek fatura bulunamadi.</musteri_verileri>"

        lines = [
            "Fatura PDF indirme icin asagidaki faturalariniz listelenmistir.",
            "Musteri, fatura indirmek istediginde /api/customer/invoice/token endpoint'ini "
            "invoice_id parametresi ile cagirmali, donen token ile /api/customer/invoice/download "
            "endpoint'inden PDF'i indirmelidir.",
            "",
            "Mevcut faturalar:",
        ]
        for inv in invoices:
            state_tr = _INVOICE_STATE_TR.get(inv.state, inv.state)
            date = inv.date[:10] if inv.date else "-"
            lines.append(
                f"- {inv.name} (ID: {inv.id}) | Tarih: {date} | Durum: {state_tr} | "
                f"Tutar: {inv.amount_total:,.2f} {inv.currency}"
            )

        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _format_payments(self, partner_id: int) -> str:
        payments = await self.odoo.get_partner_payments(partner_id, limit=15)
        if not payments:
            return "<musteri_verileri>Kayitli odeme bulunamadi.</musteri_verileri>"

        lines = ["Odeme Gecmisi:"]
        for p in payments:
            date = p.date[:10] if p.date else "-"
            ptype = "Gelen" if p.payment_type == "inbound" else "Giden"
            lines.append(
                f"- {p.name} | Tarih: {date} | Tip: {ptype} | "
                f"Tutar: {p.amount:,.2f} {p.currency} | Durum: {p.state}"
            )
        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _format_deliveries(self, partner_id: int) -> str:
        deliveries = await self.odoo.get_partner_deliveries(partner_id, limit=10)
        if not deliveries:
            return "<musteri_verileri>Kayitli teslimat bulunamadi.</musteri_verileri>"

        lines = ["Teslimat/Kargo Bilgileri:"]
        for d in deliveries:
            state_tr = _DELIVERY_STATE_TR.get(d.state, d.state)
            sched = d.scheduled_date[:10] if d.scheduled_date else "-"
            done = d.date_done[:10] if d.date_done else "-"
            tracking = f" | Takip: {d.tracking_ref}" if d.tracking_ref else ""
            carrier = f" | Kargo: {d.carrier}" if d.carrier else ""
            lines.append(
                f"- {d.name} | Kaynak: {d.origin or '-'} | Durum: {state_tr} | "
                f"Plan: {sched} | Teslim: {done}{carrier}{tracking}"
            )
        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _format_profile(self, partner_id: int) -> str:
        partner = await self.odoo.get_partner(partner_id)
        if not partner:
            return "<musteri_verileri>Profil bilgisi alinamadi.</musteri_verileri>"

        lines = [
            "Musteri Profili:",
            f"  Ad: {partner.name}",
            f"  E-posta: {partner.email or '-'}",
            f"  Telefon: {partner.phone or '-'}",
            f"  Mobil: {partner.mobile or '-'}",
        ]
        if partner.company_name:
            lines.append(f"  Firma: {partner.company_name}")
        if partner.vat:
            lines.append(f"  Vergi No: {partner.vat}")

        addr_parts = [p for p in [partner.street, partner.street2, partner.city, partner.state, partner.zip, partner.country] if p]
        if addr_parts:
            lines.append(f"  Adres: {', '.join(addr_parts)}")

        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _format_tickets(self, partner_id: int) -> str:
        tickets = await self.odoo.get_partner_tickets(partner_id, limit=10)
        if not tickets:
            return "<musteri_verileri>Kayitli destek talebi bulunamadi.</musteri_verileri>"

        lines = ["Destek Talepleri:"]
        for t in tickets:
            date = t.create_date[:10] if t.create_date else "-"
            lines.append(
                f"- #{t.id} {t.name} | Asama: {t.stage or '-'} | "
                f"Oncelik: {t.priority or '-'} | Tarih: {date}"
            )
        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _format_spending_report(self, partner_id: int) -> str:
        report = await self.odoo.get_spending_report(partner_id)

        state_lines = []
        for state, count in report.orders_by_state.items():
            state_tr = _ORDER_STATE_TR.get(state, state)
            state_lines.append(f"  {state_tr}: {count}")

        lines = [
            "Harcama Raporu:",
            f"  Toplam Siparis: {report.total_orders}",
            f"  Toplam Harcama: {report.total_spent:,.2f} {report.currency}",
            f"  Toplam Faturalanan: {report.total_invoiced:,.2f} {report.currency}",
            f"  Toplam Odenen: {report.total_paid:,.2f} {report.currency}",
            f"  Odenmemis Bakiye: {report.total_outstanding:,.2f} {report.currency}",
        ]
        if state_lines:
            lines.append("  Siparis Durumlari:")
            lines.extend(state_lines)

        return f"<musteri_verileri>\n" + "\n".join(lines) + "\n</musteri_verileri>"

    async def _gather_context(
        self, user_message: str, intent: Intent, source_group_id: str | None = None,
        visitor_id: str | None = None,
    ) -> tuple[str, list[dict], str]:
        """Gather context from RAG and product DB in parallel."""
        context = ""
        sources = []
        product_context = ""

        perms = await self._load_source_group_permissions(source_group_id)
        tasks = []

        if intent.needs_rag and perms.get("rag_enabled", True):
            tasks.append(("rag", self._get_rag_context(user_message, source_group_id)))

        # Product DB for price, stock, product info, and hybrid intents
        if intent in (
            Intent.PRICE_INQUIRY, Intent.STOCK_CHECK,
            Intent.PRODUCT_INFO, Intent.HYBRID,
        ) and perms.get("product_db_enabled", True):
            # Check for customer-specific pricelist and session (for guest_mode)
            pricelist_info = None
            has_session = False
            if self.customer_session and visitor_id:
                session = await self.customer_session.get_session(visitor_id)
                if session:
                    has_session = True
                    if session.pricelist_id:
                        pricelist_info = {
                            "pricelist_name": session.pricelist_name,
                            "discount_percent": session.discount_percent,
                        }
            guest_mode = not has_session  # guests see no price and Var/Yok for stock
            tasks.append(("product_db", self._get_product_context(user_message, intent, pricelist_info, guest_mode)))

        if tasks:
            results = await asyncio.gather(
                *[t[1] for t in tasks], return_exceptions=True
            )
            for (name, _), result in zip(tasks, results):
                if isinstance(result, Exception):
                    logger.error("Error in %s: %s", name, result)
                    continue
                if name == "rag":
                    context, sources = result
                elif name == "product_db":
                    product_context = result

        return context, sources, product_context

    async def _get_rag_context(self, query: str, source_group_id: str | None = None) -> tuple[str, list[dict]]:
        chunks = await self.rag.search(query, top_k=10, score_threshold=0.5, source_group_id=source_group_id)
        context = self.rag.build_context(chunks, max_chunks=5)
        sources = self.rag.get_sources(chunks, max_chunks=5)
        return context, sources

    async def _get_product_context(self, message: str, intent: Intent, pricelist_info: dict | None = None, guest_mode: bool = False) -> str:
        """Query product database and format as context text."""
        products = []

        if intent == Intent.PRICE_INQUIRY:
            products = await self.product_db.get_product_price(message, limit=10)
        elif intent == Intent.STOCK_CHECK:
            products = await self.product_db.get_stock_info(message, limit=10)
        elif intent in (Intent.PRODUCT_INFO, Intent.HYBRID):
            # 1. Try regex-based food category detection
            food_cat = ProductDBService._detect_food_category(message)
            # 2. AI fallback for foods not covered by regex
            if not food_cat:
                try:
                    food_cat = await self.llm.classify_food_category(message)
                except Exception as e:
                    logger.warning("AI food category fallback failed: %s", e)
                    food_cat = None
            products = await self.product_db.search_products(message, limit=10, food_category=food_cat)

        if not products:
            return ""

        return self.product_db.format_products_context(products, pricelist_info, guest_mode=guest_mode)

    def _extract_order_ref(self, message: str) -> str | None:
        patterns = [
            r"S\d{5}",
            r"SO\d{4,}",
            r"#?\d{4,8}",
        ]
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return match.group(0)
        return None

    def _build_catalog_response(self, user_message: str) -> str:
        is_english = self._is_english_message(user_message)
        tr_url = settings.odoo_catalog_url.strip()
        en_url = settings.odoo_catalog_url_en.strip()

        if is_english:
            if en_url:
                return (
                    f"Here is our English catalog PDF link:\n{en_url}\n\n"
                    "Would you like me to send the catalog to your email address as well?"
                )
            if tr_url:
                return (
                    f"I currently don't have an English catalog file, so here is our Turkish catalog PDF link:\n{tr_url}\n\n"
                    "Would you like me to send the catalog to your email address as well?"
                )
            return (
                "I don't have a catalog link configured right now.\n\n"
                "Would you like me to send the catalog to your email address as well once it is available?"
            )

        if tr_url:
            return (
                f"PDF katalog linkimiz:\n{tr_url}\n\n"
                "Katalogu e-posta adresinize de gondereyim mi?"
            )
        if en_url:
            return (
                f"Turkce katalog dosyasi su an mevcut degil, bu nedenle Ingilizce katalog PDF linkini paylasiyorum:\n{en_url}\n\n"
                "Katalogu e-posta adresinize de gondereyim mi?"
            )
        return (
            "Su anda katalog linki tanimli degil.\n\n"
            "Katalog hazir oldugunda e-posta adresinize gondermemi ister misiniz?"
        )

    def _is_english_message(self, message: str) -> bool:
        lower = message.lower()

        english_markers = re.compile(
            r"\b(catalog|brochure|pdf|send|share|english|please|can you|could you|would you)\b"
        )
        turkish_markers = re.compile(
            r"\b(katalog|bro[sş][uü]r|g[oö]nder|payla[sş]|t[uü]rk[cç]e|l[uü]tfen)\b"
        )

        if turkish_markers.search(lower):
            return False
        if english_markers.search(lower):
            return True

        # If no clear markers, treat Turkish-specific characters as Turkish;
        # default to Turkish to align with product behavior.
        if re.search(r"[çğıöşü]", lower):
            return False
        return False

    async def _save_and_return(
        self,
        conv: Conversation,
        response_text: str,
        intent: Intent,
        sources: list,
        odoo_data: dict | None,
    ) -> dict:
        msg = await self._save_assistant_message(
            conv.id, response_text, intent, sources, odoo_data
        )
        return {
            "conversation_id": str(conv.id),
            "message_id": str(msg.id),
            "content": response_text,
            "intent": intent.value,
            "sources": sources,
        }

    async def _save_assistant_message(
        self,
        conversation_id: uuid.UUID,
        content: str,
        intent: Intent,
        sources: list,
        odoo_data: dict | None,
    ) -> Message:
        msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=content,
            intent=intent.value,
            sources=sources,
            odoo_data=odoo_data,
            token_count=len(content.split()),
        )
        self.db.add(msg)
        await self.db.flush()
        return msg
