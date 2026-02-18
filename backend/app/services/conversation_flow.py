"""Conversation flow manager for multi-step interactions (OTP, order creation, etc.)."""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum

import redis.asyncio as redis

logger = logging.getLogger(__name__)

FLOW_TTL = 1800  # 30 minutes
CANCEL_WORDS = {"iptal", "vazgec", "vazgeç", "cancel", "kapat"}
RESTART_WORDS = {"bastan", "baştan", "yeniden", "restart"}


class FlowType(StrEnum):
    OTP_AUTH = "otp_auth"
    ORDER_CREATE = "order_create"
    QUOTATION_CREATE = "quotation_create"
    ADDRESS_UPDATE = "address_update"
    TICKET_CREATE = "ticket_create"
    ORDER_CANCEL = "order_cancel"
    COMPLAINT = "complaint"
    FIND_DEALER = "find_dealer"


@dataclass
class FlowStepResult:
    """Result returned by a flow step."""
    message: str
    flow_completed: bool = False
    flow_cancelled: bool = False
    data: dict = field(default_factory=dict)


@dataclass
class ConversationFlow:
    """Represents the state of an active multi-step flow."""
    flow_type: str
    step: str
    data: dict = field(default_factory=dict)
    conversation_id: str = ""


class FlowHandler(ABC):
    """Abstract base class for flow handlers."""

    @property
    @abstractmethod
    def flow_type(self) -> FlowType:
        ...

    @abstractmethod
    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        """Process the current step and return the result."""
        ...

    @abstractmethod
    def initial_step(self) -> str:
        """Return the name of the first step in this flow."""
        ...


class FlowManager:
    """Manages active conversation flows stored in Redis."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self._handlers: dict[str, FlowHandler] = {}

    def register_handler(self, handler: FlowHandler) -> None:
        self._handlers[handler.flow_type] = handler

    def _flow_key(self, conversation_id: str) -> str:
        return f"flow:{conversation_id}"

    async def get_active_flow(self, conversation_id: str) -> ConversationFlow | None:
        """Get the active flow for a conversation, if any."""
        key = self._flow_key(conversation_id)
        data = await self.redis.get(key)
        if not data:
            return None

        flow_data = json.loads(data)
        return ConversationFlow(
            flow_type=flow_data["flow_type"],
            step=flow_data["step"],
            data=flow_data.get("data", {}),
            conversation_id=conversation_id,
        )

    async def start_flow(
        self,
        conversation_id: str,
        flow_type: FlowType,
        initial_data: dict | None = None,
    ) -> ConversationFlow:
        """Start a new flow for a conversation."""
        handler = self._handlers.get(flow_type)
        if not handler:
            raise ValueError(f"No handler for flow type: {flow_type}")

        flow = ConversationFlow(
            flow_type=flow_type,
            step=handler.initial_step(),
            data=initial_data or {},
            conversation_id=conversation_id,
        )

        await self._save_flow(conversation_id, flow)
        logger.info("Flow started: type=%s, conv=%s", flow_type, conversation_id)
        return flow

    async def process_step(
        self,
        conversation_id: str,
        user_message: str,
        visitor_id: str,
    ) -> FlowStepResult | None:
        """Process the current step of an active flow."""
        flow = await self.get_active_flow(conversation_id)
        if not flow:
            return None

        # Check for cancel/restart commands
        lower = user_message.strip().lower()
        if lower in CANCEL_WORDS:
            await self.cancel_flow(conversation_id)
            return FlowStepResult(
                message="Islem iptal edildi. Size baska nasil yardimci olabilirim?",
                flow_cancelled=True,
            )
        if lower in RESTART_WORDS:
            handler = self._handlers.get(flow.flow_type)
            if handler:
                flow.step = handler.initial_step()
                flow.data = {}
                await self._save_flow(conversation_id, flow)
                return FlowStepResult(
                    message="Islem bastan basladi.",
                )

        handler = self._handlers.get(flow.flow_type)
        if not handler:
            await self.cancel_flow(conversation_id)
            return FlowStepResult(
                message="Bir hata olustu. Islem iptal edildi.",
                flow_cancelled=True,
            )

        result = await handler.process_step(flow, user_message, visitor_id)

        if result.flow_completed or result.flow_cancelled:
            await self.cancel_flow(conversation_id)
        else:
            # Save updated flow state
            await self._save_flow(conversation_id, flow)

        return result

    async def cancel_flow(self, conversation_id: str) -> None:
        """Cancel an active flow."""
        key = self._flow_key(conversation_id)
        await self.redis.delete(key)
        logger.info("Flow cancelled: conv=%s", conversation_id)

    async def _save_flow(self, conversation_id: str, flow: ConversationFlow) -> None:
        """Save flow state to Redis."""
        key = self._flow_key(conversation_id)
        data = json.dumps({
            "flow_type": flow.flow_type,
            "step": flow.step,
            "data": flow.data,
        })
        await self.redis.set(key, data, ex=FLOW_TTL)
