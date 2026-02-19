"""Quotation request flow handler.

Steps: await_details → await_confirm → created

Simplified flow for requesting a price quotation.
"""

import logging

from app.services.conversation_flow import (
    ConversationFlow,
    FlowHandler,
    FlowStepResult,
    FlowType,
)
from app.services.customer_session_service import CustomerSessionService
from app.services.odoo_service import OdooService

logger = logging.getLogger(__name__)


class QuotationFlowHandler(FlowHandler):
    """Handles quotation request: describe needs → confirm → create quotation."""

    def __init__(
        self,
        odoo_service: OdooService,
        session_service: CustomerSessionService,
    ):
        self.odoo = odoo_service
        self.session = session_service

    @property
    def flow_type(self) -> FlowType:
        return FlowType.QUOTATION_CREATE

    def initial_step(self) -> str:
        return "await_details"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        if step == "await_details":
            return await self._handle_details(flow, user_message)
        elif step == "await_confirm":
            return await self._handle_confirm(flow, user_message, visitor_id)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    async def _handle_details(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        text = user_message.strip()

        if len(text) < 5:
            return FlowStepResult(
                message=(
                    "Teklif almak icin ihtiyacinizi detayli olarak yazin.\n"
                    "Ornegin: urun adlari, miktarlar, teslimat kosullari, ozel istekler."
                ),
            )

        flow.step = "await_confirm"
        flow.data["details"] = text

        return FlowStepResult(
            message=(
                f"Teklif talebi ozeti:\n"
                f"**{text}**\n\n"
                f"Bu teklif talebini gondermek istiyor musunuz? (**evet** / **hayir**)"
            ),
        )

    async def _handle_confirm(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        if text in ("hayir", "hayır", "no", "h"):
            return FlowStepResult(
                message="Teklif talebi iptal edildi.",
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
            details = flow.data.get("details", "")
            note = f"Musteri Teklif Talebi:\n{details}"

            result = await self.odoo.create_quotation(
                partner_id=session.partner_id,
                lines=[],
                notes=note,
            )

            return FlowStepResult(
                message=(
                    f"Teklif talebiniz basariyla olusturuldu!\n"
                    f"- **Teklif No:** {result.order_ref}\n\n"
                    f"Satis ekibimiz en kisa surede fiyat teklifinizi hazirlayacaktir."
                ),
                flow_completed=True,
                data={"order_id": result.order_id, "order_ref": result.order_ref},
            )
        except Exception as e:
            logger.error("Quotation creation error (partner_id=%s): %s", session.partner_id, e, exc_info=True)
            return FlowStepResult(
                message="Teklif talebi olusturulurken bir hata olustu. Lutfen daha sonra tekrar deneyin.",
                flow_cancelled=True,
            )
