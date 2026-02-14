"""Address/profile update flow handler.

Steps: await_field → await_value → await_confirm → updated
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

# Fields that can be updated
UPDATABLE_FIELDS = {
    "telefon": ("phone", "Telefon"),
    "phone": ("phone", "Telefon"),
    "mobil": ("mobile", "Mobil Telefon"),
    "cep": ("mobile", "Mobil Telefon"),
    "mobile": ("mobile", "Mobil Telefon"),
    "email": ("email", "E-posta"),
    "e-posta": ("email", "E-posta"),
    "eposta": ("email", "E-posta"),
    "adres": ("street", "Adres"),
    "sokak": ("street", "Adres"),
    "cadde": ("street", "Adres"),
    "street": ("street", "Adres"),
    "adres2": ("street2", "Adres (2. satir)"),
    "sehir": ("city", "Sehir"),
    "şehir": ("city", "Sehir"),
    "il": ("city", "Sehir"),
    "city": ("city", "Sehir"),
    "posta": ("zip", "Posta Kodu"),
    "posta kodu": ("zip", "Posta Kodu"),
    "zip": ("zip", "Posta Kodu"),
}

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
PHONE_REGEX = re.compile(r"^[\d\s\-\+\(\)]{7,20}$")


class AddressFlowHandler(FlowHandler):
    """Handles profile/address update: select field → enter value → confirm → update."""

    def __init__(
        self,
        odoo_service: OdooService,
        session_service: CustomerSessionService,
    ):
        self.odoo = odoo_service
        self.session = session_service

    @property
    def flow_type(self) -> FlowType:
        return FlowType.ADDRESS_UPDATE

    def initial_step(self) -> str:
        return "await_field"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        if step == "await_field":
            return await self._handle_field(flow, user_message)
        elif step == "await_value":
            return await self._handle_value(flow, user_message)
        elif step == "await_confirm":
            return await self._handle_confirm(flow, user_message, visitor_id)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    async def _handle_field(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        field_info = UPDATABLE_FIELDS.get(text)
        if not field_info:
            # Try partial match
            for key, info in UPDATABLE_FIELDS.items():
                if key in text:
                    field_info = info
                    break

        if not field_info:
            return FlowStepResult(
                message=(
                    "Lutfen guncellemek istediginiz alani secin:\n"
                    "- **telefon** - Sabit telefon\n"
                    "- **mobil** - Cep telefonu\n"
                    "- **email** - E-posta adresi\n"
                    "- **adres** - Sokak/cadde adresi\n"
                    "- **sehir** - Sehir\n"
                    "- **posta kodu** - Posta kodu"
                ),
            )

        field_name, field_label = field_info
        flow.step = "await_value"
        flow.data["field_name"] = field_name
        flow.data["field_label"] = field_label

        return FlowStepResult(
            message=f"Lutfen yeni **{field_label}** bilginizi yazin.",
        )

    async def _handle_value(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        value = user_message.strip()
        field_name = flow.data.get("field_name", "")
        field_label = flow.data.get("field_label", "")

        if not value:
            return FlowStepResult(
                message=f"Lutfen gecerli bir {field_label} degeri girin.",
            )

        # Field-specific validation
        if field_name == "email" and not EMAIL_REGEX.match(value):
            return FlowStepResult(
                message="Gecerli bir e-posta adresi giriniz. Ornegin: isim@firma.com",
            )

        if field_name in ("phone", "mobile") and not PHONE_REGEX.match(value):
            return FlowStepResult(
                message="Gecerli bir telefon numarasi giriniz. Ornegin: +90 532 123 4567",
            )

        flow.step = "await_confirm"
        flow.data["new_value"] = value

        return FlowStepResult(
            message=(
                f"Guncelleme ozeti:\n"
                f"- **Alan:** {field_label}\n"
                f"- **Yeni deger:** {value}\n\n"
                f"Onayliyor musunuz? (**evet** / **hayir**)"
            ),
        )

    async def _handle_confirm(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        text = user_message.strip().lower()

        if text in ("hayir", "hayır", "no", "h"):
            return FlowStepResult(
                message="Guncelleme islemi iptal edildi.",
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
            field_name = flow.data["field_name"]
            field_label = flow.data["field_label"]
            new_value = flow.data["new_value"]

            result = await self.odoo.update_partner(
                partner_id=session.partner_id,
                vals={field_name: new_value},
            )

            if result:
                return FlowStepResult(
                    message=f"**{field_label}** bilginiz basariyla guncellendi.",
                    flow_completed=True,
                    data={"field": field_name, "value": new_value},
                )
            else:
                return FlowStepResult(
                    message="Guncelleme sirasinda bir hata olustu. Lutfen daha sonra tekrar deneyin.",
                    flow_cancelled=True,
                )
        except Exception as e:
            logger.error("Address update error: %s", e)
            return FlowStepResult(
                message="Guncelleme sirasinda bir hata olustu. Lutfen daha sonra tekrar deneyin.",
                flow_cancelled=True,
            )
