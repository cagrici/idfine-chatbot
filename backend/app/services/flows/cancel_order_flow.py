"""Order cancellation request flow handler.

Steps: await_order_ref → await_reason → await_confirm → requested
"""

import logging
import re

from app.services.conversation_flow import (
    ConversationFlow,
    FlowHandler,
    FlowStepResult,
    FlowType,
)
from app.services.customer_session_service import CustomerSessionService
from app.services.odoo_service import OdooService

logger = logging.getLogger(__name__)

ORDER_REF_PATTERN = re.compile(r"S\d{5}|SO\d{4,}", re.IGNORECASE)


class CancelOrderFlowHandler(FlowHandler):
    """Handles order cancellation: order_ref → reason → confirm → request."""

    def __init__(
        self,
        odoo_service: OdooService,
        session_service: CustomerSessionService,
    ):
        self.odoo = odoo_service
        self.session = session_service

    @property
    def flow_type(self) -> FlowType:
        return FlowType.ORDER_CANCEL

    def initial_step(self) -> str:
        return "await_order_ref"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        if step == "await_order_ref":
            return await self._handle_order_ref(flow, user_message, visitor_id)
        elif step == "await_reason":
            return await self._handle_reason(flow, user_message)
        elif step == "await_confirm":
            return await self._handle_confirm(flow, user_message, visitor_id)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    async def _handle_order_ref(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        text = user_message.strip()

        # Try to extract order reference
        match = ORDER_REF_PATTERN.search(text)
        if not match:
            return FlowStepResult(
                message="Lutfen iptal etmek istediginiz siparis numarasini yazin. Ornegin: **S00123** veya **SO0001**",
            )

        order_ref = match.group(0).upper()

        # Verify order belongs to customer
        session = await self.session.get_session(visitor_id)
        if not session:
            return FlowStepResult(
                message="Oturum suresi dolmus. Lutfen tekrar giris yapin.",
                flow_cancelled=True,
            )

        orders = await self.odoo.get_partner_orders(session.partner_id, limit=100)
        order = next((o for o in orders if order_ref in o.name.upper()), None)

        if not order:
            return FlowStepResult(
                message=f"**{order_ref}** numarali siparis bulunamadi. Lutfen siparis numaranizi kontrol edip tekrar yazin.",
            )

        # Check if order can be cancelled
        if order.state in ("done", "cancel"):
            state_label = "tamamlanmis" if order.state == "done" else "zaten iptal edilmis"
            return FlowStepResult(
                message=f"**{order.name}** siparisi {state_label} durumda ve iptal edilemez.",
                flow_cancelled=True,
            )

        flow.step = "await_reason"
        flow.data["order_id"] = order.id
        flow.data["order_name"] = order.name
        flow.data["order_amount"] = order.amount_total
        flow.data["order_currency"] = order.currency

        return FlowStepResult(
            message=(
                f"Siparis bulundu: **{order.name}** - {order.amount_total:,.2f} {order.currency}\n\n"
                f"Lutfen iptal nedeninizi yazin."
            ),
        )

    async def _handle_reason(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        reason = user_message.strip()
        if len(reason) < 5:
            return FlowStepResult(
                message="Lutfen iptal nedeninizi biraz daha detayli yazin (en az 5 karakter).",
            )

        flow.step = "await_confirm"
        flow.data["reason"] = reason

        order_name = flow.data.get("order_name", "")
        amount = flow.data.get("order_amount", 0)
        currency = flow.data.get("order_currency", "TRY")

        return FlowStepResult(
            message=(
                f"Iptal ozeti:\n"
                f"- **Siparis:** {order_name} ({amount:,.2f} {currency})\n"
                f"- **Neden:** {reason}\n\n"
                f"Iptal talebini onayliyor musunuz? (**evet** / **hayir**)"
            ),
        )

    async def _handle_confirm(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        if text in ("hayir", "hayır", "no", "h"):
            return FlowStepResult(
                message="Siparis iptal islemi vazgecildi.",
                flow_cancelled=True,
            )

        if text not in ("evet", "yes", "e", "ok", "tamam", "onay", "onayla"):
            return FlowStepResult(
                message="Lutfen **evet** veya **hayir** yazin.",
            )

        session = await self.session.get_session(visitor_id)
        if not session:
            return FlowStepResult(
                message="Oturum suresi dolmus. Lutfen tekrar giris yapin.",
                flow_cancelled=True,
            )

        try:
            order_id = flow.data["order_id"]
            reason = flow.data.get("reason", "")
            order_name = flow.data.get("order_name", "")

            result = await self.odoo.request_order_cancellation(
                order_id=order_id,
                partner_id=session.partner_id,
                reason=reason,
            )

            if result:
                return FlowStepResult(
                    message=f"**{order_name}** siparisi icin iptal talebi olusturuldu.\n"
                            f"Talebiniz incelendikten sonra size bilgi verilecektir.",
                    flow_completed=True,
                    data={"order_id": order_id, "order_name": order_name},
                )
            else:
                return FlowStepResult(
                    message="Iptal talebi olusturulurken bir hata olustu. Lutfen musteri hizmetleri ile iletisime gecin.",
                    flow_cancelled=True,
                )
        except Exception as e:
            logger.error("Order cancellation error: %s", e)
            return FlowStepResult(
                message="Iptal talebi olusturulurken bir hata olustu. Lutfen musteri hizmetleri ile iletisime gecin.",
                flow_cancelled=True,
            )
