"""Support ticket creation flow handler.

Steps: await_subject → await_description → await_priority → await_confirm → created
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

PRIORITY_MAP = {
    "1": ("Dusuk", "1"),
    "2": ("Normal", "2"),
    "3": ("Yuksek", "3"),
    "dusuk": ("Dusuk", "1"),
    "düşük": ("Dusuk", "1"),
    "normal": ("Normal", "2"),
    "orta": ("Normal", "2"),
    "yuksek": ("Yuksek", "3"),
    "yüksek": ("Yuksek", "3"),
    "acil": ("Yuksek", "3"),
    "low": ("Dusuk", "1"),
    "medium": ("Normal", "2"),
    "high": ("Yuksek", "3"),
    "urgent": ("Yuksek", "3"),
}


class TicketFlowHandler(FlowHandler):
    """Handles support ticket creation: subject → description → priority → confirm → create."""

    def __init__(
        self,
        odoo_service: OdooService,
        session_service: CustomerSessionService,
    ):
        self.odoo = odoo_service
        self.session = session_service

    @property
    def flow_type(self) -> FlowType:
        return FlowType.TICKET_CREATE

    def initial_step(self) -> str:
        return "await_subject"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        if step == "await_subject":
            return await self._handle_subject(flow, user_message)
        elif step == "await_description":
            return await self._handle_description(flow, user_message)
        elif step == "await_priority":
            return await self._handle_priority(flow, user_message)
        elif step == "await_confirm":
            return await self._handle_confirm(flow, user_message, visitor_id)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    async def _handle_subject(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        subject = user_message.strip()
        if len(subject) < 3:
            return FlowStepResult(
                message="Lutfen destek talebiniz icin bir konu basligi yazin (en az 3 karakter).",
            )

        flow.step = "await_description"
        flow.data["subject"] = subject

        return FlowStepResult(
            message=f"Konu: **{subject}**\n\nSimdi lutfen sorununuzu detayli olarak aciklayiniz.",
        )

    async def _handle_description(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        description = user_message.strip()
        if len(description) < 10:
            return FlowStepResult(
                message="Lutfen sorununuzu biraz daha detayli aciklayiniz (en az 10 karakter).",
            )

        flow.step = "await_priority"
        flow.data["description"] = description

        return FlowStepResult(
            message=(
                "Tesekkurler. Lutfen oncelik seviyesini secin:\n"
                "- **1** - Dusuk\n"
                "- **2** - Normal\n"
                "- **3** - Yuksek/Acil"
            ),
        )

    async def _handle_priority(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()
        priority_info = PRIORITY_MAP.get(text)

        if not priority_info:
            return FlowStepResult(
                message="Lutfen gecerli bir oncelik secin: **1** (Dusuk), **2** (Normal) veya **3** (Yuksek).",
            )

        priority_label, priority_code = priority_info
        flow.step = "await_confirm"
        flow.data["priority"] = priority_code
        flow.data["priority_label"] = priority_label

        subject = flow.data.get("subject", "")
        description = flow.data.get("description", "")

        return FlowStepResult(
            message=(
                f"Destek talebi ozeti:\n"
                f"- **Konu:** {subject}\n"
                f"- **Aciklama:** {description[:100]}{'...' if len(description) > 100 else ''}\n"
                f"- **Oncelik:** {priority_label}\n\n"
                f"Onayliyor musunuz? (**evet** / **hayir**)"
            ),
        )

    async def _handle_confirm(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        if text in ("hayir", "hayır", "no", "h"):
            return FlowStepResult(
                message="Destek talebi olusturma iptal edildi.",
                flow_cancelled=True,
            )

        if text not in ("evet", "yes", "e", "ok", "tamam", "onay", "onayla"):
            return FlowStepResult(
                message="Lutfen **evet** veya **hayir** yazin.",
            )

        # Get partner_id from session
        session = await self.session.get_session(visitor_id)
        if not session:
            return FlowStepResult(
                message="Oturum suresi dolmus. Lutfen tekrar giris yapin.",
                flow_cancelled=True,
            )

        try:
            ticket_id = await self.odoo.create_ticket(
                partner_id=session.partner_id,
                subject=flow.data["subject"],
                description=flow.data["description"],
                priority=flow.data.get("priority", "1"),
            )

            if ticket_id:
                return FlowStepResult(
                    message=f"Destek talebiniz basariyla olusturuldu! (Talep No: #{ticket_id})\n"
                            f"Talebiniz en kisa surede degerlendirilecektir.",
                    flow_completed=True,
                    data={"ticket_id": ticket_id},
                )
            else:
                return FlowStepResult(
                    message="Destek talebi olusturulurken bir hata olustu. Lutfen daha sonra tekrar deneyin.",
                    flow_cancelled=True,
                )
        except Exception as e:
            logger.error("Ticket creation error: %s", e)
            return FlowStepResult(
                message="Destek talebi olusturulurken bir hata olustu. Lutfen daha sonra tekrar deneyin.",
                flow_cancelled=True,
            )
