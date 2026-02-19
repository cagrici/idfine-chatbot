"""Complaint flow handler — collects complaint info, sends email via SMTP, optionally creates Odoo ticket.

Steps: await_name → await_contact → await_description → await_confirm → done
If customer is authenticated, name/contact steps are skipped automatically.
"""

import logging
from datetime import datetime, timezone

from app.services.conversation_flow import (
    ConversationFlow,
    FlowHandler,
    FlowStepResult,
    FlowType,
)
from app.services.customer_session_service import CustomerSessionService
from app.services.email_service import EmailService
from app.services.odoo_service import OdooService

logger = logging.getLogger(__name__)

COMPLAINT_EMAIL_TO = "destek@idfine.com.tr"


class ComplaintFlowHandler(FlowHandler):
    """Collects complaint → sends email via SMTP → optionally creates Odoo ticket."""

    def __init__(
        self,
        odoo_service: OdooService,
        session_service: CustomerSessionService,
        email_service: EmailService | None = None,
    ):
        self.odoo = odoo_service
        self.session = session_service
        self.email = email_service or EmailService()

    @property
    def flow_type(self) -> FlowType:
        return FlowType.COMPLAINT

    def initial_step(self) -> str:
        return "await_name"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        # On first entry, check if customer is authenticated and pre-fill data
        if step == "await_name" and "auth_checked" not in flow.data:
            flow.data["auth_checked"] = True
            flow.data["conversation_id"] = flow.conversation_id
            session = await self.session.get_session(visitor_id) if visitor_id else None
            if session:
                flow.data["partner_id"] = session.partner_id
                try:
                    partner = await self.odoo.get_partner(session.partner_id)
                    if partner:
                        flow.data["name"] = partner.name or ""
                        flow.data["contact"] = partner.email or partner.phone or ""
                        if flow.data["name"] and flow.data["contact"]:
                            flow.step = "await_description"
                            return FlowStepResult(
                                message=(
                                    f"Sayin **{flow.data['name']}**, sikayetinizi almak istiyoruz.\n"
                                    "Lutfen sikayetinizi detayli olarak yaziniz."
                                ),
                            )
                except Exception as e:
                    logger.warning("Could not fetch partner info from Odoo: %s", e)

        if step == "await_name":
            return await self._handle_name(flow, user_message)
        elif step == "await_contact":
            return await self._handle_contact(flow, user_message)
        elif step == "await_description":
            return await self._handle_description(flow, user_message)
        elif step == "await_confirm":
            return await self._handle_confirm(flow, user_message)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    async def _handle_name(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        name = user_message.strip()
        if len(name) < 2:
            return FlowStepResult(
                message="Lutfen adinizi ve soyadinizi yaziniz.",
            )

        flow.step = "await_contact"
        flow.data["name"] = name

        return FlowStepResult(
            message=(
                f"Tesekkurler, **{name}**.\n"
                "Lutfen iletisim bilginizi yaziniz (e-posta veya telefon numarasi)."
            ),
        )

    async def _handle_contact(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        contact = user_message.strip()
        if len(contact) < 5:
            return FlowStepResult(
                message="Lutfen gecerli bir e-posta adresi veya telefon numarasi yaziniz.",
            )

        flow.step = "await_description"
        flow.data["contact"] = contact

        return FlowStepResult(
            message="Tesekkurler. Simdi lutfen sikayetinizi detayli olarak yaziniz.",
        )

    async def _handle_description(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        description = user_message.strip()
        if len(description) < 10:
            return FlowStepResult(
                message="Lutfen sikayetinizi biraz daha detayli aciklayiniz (en az 10 karakter).",
            )

        flow.step = "await_confirm"
        flow.data["description"] = description

        name = flow.data.get("name", "")
        contact = flow.data.get("contact", "")

        return FlowStepResult(
            message=(
                f"Sikayet ozeti:\n"
                f"- **Ad Soyad:** {name}\n"
                f"- **Iletisim:** {contact}\n"
                f"- **Sikayet:** {description[:150]}{'...' if len(description) > 150 else ''}\n\n"
                f"Gondermek istediginize emin misiniz? (**evet** / **hayir**)"
            ),
        )

    async def _handle_confirm(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        if text in ("hayir", "hayır", "no", "h"):
            return FlowStepResult(
                message="Sikayet islemi iptal edildi.",
                flow_cancelled=True,
            )

        if text not in ("evet", "yes", "e", "ok", "tamam", "onay", "onayla"):
            return FlowStepResult(
                message="Lutfen **evet** veya **hayir** yazin.",
            )

        name = flow.data.get("name", "Bilinmiyor")
        contact = flow.data.get("contact", "Belirtilmedi")
        description = flow.data.get("description", "")
        partner_id = flow.data.get("partner_id")
        conv_id = flow.data.get("conversation_id", "")
        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")

        ticket_id = None
        ticket_info = ""

        # Create Odoo helpdesk ticket if customer is authenticated
        if partner_id:
            try:
                ticket_id = await self.odoo.create_ticket(
                    partner_id=partner_id,
                    subject=f"Musteri Sikayeti - {name}",
                    description=description,
                    priority="2",
                )
            except Exception as e:
                logger.error("Failed to create complaint ticket in Odoo: %s", e)

        if ticket_id:
            ticket_info = f"<p><strong>Talep No:</strong> #{ticket_id}</p>"

        # Send email via SMTP
        body_html = (
            "<h2>Yeni Musteri Sikayeti</h2>"
            f"<p><strong>Ad Soyad:</strong> {name}</p>"
            f"<p><strong>Iletisim:</strong> {contact}</p>"
            f"<p><strong>Tarih:</strong> {now} (UTC)</p>"
            f"<p><strong>Konusma ID:</strong> {conv_id}</p>"
            f"{ticket_info}"
            "<hr>"
            f"<p><strong>Sikayet Detayi:</strong></p>"
            f"<p>{description}</p>"
        )

        sent = self.email.send(
            to=COMPLAINT_EMAIL_TO,
            subject=f"Yeni Musteri Sikayeti - {name}",
            body_html=body_html,
        )

        if not sent:
            logger.error("Complaint email could not be sent for conv %s", conv_id)
            return FlowStepResult(
                message=(
                    "Sikayetiniz iletilirken bir hata olustu. "
                    "Lutfen daha sonra tekrar deneyin veya "
                    "destek@idfine.com.tr adresine e-posta gonderiniz."
                ),
                flow_cancelled=True,
            )

        response = (
            "Sikayetiniz basariyla alindi ve destek ekibimize iletildi.\n"
            "En kisa surede sizinle iletisime gecilebilmesi icin bilgileriniz kaydedilmistir."
        )
        if ticket_id:
            response += f"\n\n**Talep No:** #{ticket_id}"

        return FlowStepResult(
            message=response,
            flow_completed=True,
            data={"ticket_id": ticket_id},
        )
