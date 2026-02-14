"""Order/quotation creation flow handler.

Steps: await_items → await_notes → await_confirm → created

This flow creates a sale.order quotation in Odoo.
The user describes what they want, the bot parses product references,
and creates a quotation for review by the sales team.
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

# Pattern to extract product code + quantity: "ABC123 x 10" or "ABC123 10 adet"
ITEM_PATTERN = re.compile(
    r"([A-Za-z0-9\-_.]+)\s*(?:x|X|adet|ad\.?)?\s*(\d+)|"
    r"(\d+)\s*(?:x|X|adet|ad\.?)?\s*([A-Za-z0-9\-_.]+)",
    re.IGNORECASE,
)


class OrderFlowHandler(FlowHandler):
    """Handles order creation: describe items → optional notes → confirm → create quotation."""

    def __init__(
        self,
        odoo_service: OdooService,
        session_service: CustomerSessionService,
    ):
        self.odoo = odoo_service
        self.session = session_service

    @property
    def flow_type(self) -> FlowType:
        return FlowType.ORDER_CREATE

    def initial_step(self) -> str:
        return "await_items"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        if step == "await_items":
            return await self._handle_items(flow, user_message, visitor_id)
        elif step == "await_notes":
            return await self._handle_notes(flow, user_message)
        elif step == "await_confirm":
            return await self._handle_confirm(flow, user_message, visitor_id)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    async def _handle_items(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        """Parse product items from user message."""
        text = user_message.strip()

        if len(text) < 3:
            return FlowStepResult(
                message=(
                    "Siparis vermek icin urun bilgilerini yazin.\n"
                    "Ornegin: **ABC123 x 10** veya urun adlarini ve miktarlari belirtin.\n"
                    "Birden fazla urun icin her birini ayri satirda yazabilirsiniz."
                ),
            )

        # Store the raw request - the sales team will interpret it
        flow.step = "await_notes"
        flow.data["items_text"] = text

        return FlowStepResult(
            message=(
                f"Urun talebi alindi.\n\n"
                f"Eklemek istediginiz bir not var mi? (Teslimat tarihi, ozel istek vb.)\n"
                f"Not eklemek istemiyorsaniz **yok** yazin."
            ),
        )

    async def _handle_notes(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        if text in ("yok", "hayir", "hayır", "no", "-", "bos", "boş"):
            flow.data["notes"] = ""
        else:
            flow.data["notes"] = user_message.strip()

        flow.step = "await_confirm"

        items_text = flow.data.get("items_text", "")
        notes = flow.data.get("notes", "")
        notes_display = notes if notes else "(Not eklenmedi)"

        return FlowStepResult(
            message=(
                f"Siparis ozeti:\n"
                f"- **Urunler:** {items_text}\n"
                f"- **Notlar:** {notes_display}\n\n"
                f"Bu siparis talebini gondermek istiyor musunuz? (**evet** / **hayir**)"
            ),
        )

    async def _handle_confirm(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        if text in ("hayir", "hayır", "no", "h"):
            return FlowStepResult(
                message="Siparis talebi iptal edildi.",
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
            items_text = flow.data.get("items_text", "")
            notes = flow.data.get("notes", "")

            # Create a quotation with the description as a note
            # The sales team will add proper product lines
            full_note = f"Musteri Siparis Talebi:\n{items_text}"
            if notes:
                full_note += f"\n\nMusteri Notu: {notes}"

            result = await self.odoo.create_quotation(
                partner_id=session.partner_id,
                lines=[],  # Empty lines - sales team will fill in
                notes=full_note,
            )

            return FlowStepResult(
                message=(
                    f"Siparis talebiniz basariyla olusturuldu!\n"
                    f"- **Teklif No:** {result.order_ref}\n"
                    f"- **Durum:** {result.status}\n\n"
                    f"Satis ekibimiz talebinizi inceleyip sizinle iletisime gececektir."
                ),
                flow_completed=True,
                data={"order_id": result.order_id, "order_ref": result.order_ref},
            )
        except Exception as e:
            logger.error("Order creation error: %s", e)
            return FlowStepResult(
                message="Siparis talebi olusturulurken bir hata olustu. Lutfen musteri hizmetleri ile iletisime gecin.",
                flow_cancelled=True,
            )
