"""Dealer finder flow — helps users find nearby dealers by city.

Steps: await_city → show_dealers → await_contact → await_confirm → done
No authentication required.
"""

import asyncio
import logging
import re

from app.services.conversation_flow import (
    ConversationFlow,
    FlowHandler,
    FlowStepResult,
    FlowType,
)
from app.services.email_service import EmailService
from app.services.odoo_service import OdooService

logger = logging.getLogger(__name__)

# Odoo partner category IDs for dealers
# 28: ID Fine Yurtiçi Bayi, 77/78/79: T1/T2/T3, 83: Porser Yurtiçi Bayi
DEALER_CATEGORY_IDS = [28, 77, 78, 79, 83]

# Turkish character normalization for city matching
_TR_TO_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
PHONE_RE = re.compile(r"[\d\s\-\+\(\)]{7,}")


class DealerFlowHandler(FlowHandler):
    """Find dealer by city → show list → collect contact → create lead + email."""

    def __init__(
        self,
        odoo_service: OdooService,
        email_service: EmailService | None = None,
    ):
        self.odoo = odoo_service
        self.email = email_service or EmailService()

    @property
    def flow_type(self) -> FlowType:
        return FlowType.FIND_DEALER

    def initial_step(self) -> str:
        return "await_city"

    async def process_step(
        self, flow: ConversationFlow, user_message: str, visitor_id: str
    ) -> FlowStepResult:
        step = flow.step

        # First entry: load cities from Odoo and present list
        if step == "await_city" and "cities_loaded" not in flow.data:
            return await self._load_cities(flow)

        if step == "await_city":
            return await self._handle_city_selection(flow, user_message)
        elif step == "show_dealers":
            return await self._handle_dealer_selection(flow, user_message)
        elif step == "await_contact":
            return await self._handle_contact(flow, user_message)
        elif step == "await_confirm":
            return await self._handle_confirm(flow, user_message)
        else:
            return FlowStepResult(
                message="Bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

    # ── Step helpers ─────────────────────────────────────────────

    async def _load_cities(self, flow: ConversationFlow) -> FlowStepResult:
        """Fetch dealer cities from Odoo and present numbered list."""
        try:
            cities = await self._get_dealer_cities()
        except Exception as e:
            logger.error("Failed to fetch dealer cities: %s", e)
            return FlowStepResult(
                message="Bayi bilgileri yuklenirken bir hata olustu. Lutfen daha sonra tekrar deneyin.",
                flow_cancelled=True,
            )

        if not cities:
            return FlowStepResult(
                message="Su anda aktif bayi bilgisi bulunamadi. Lutfen daha sonra tekrar deneyin.",
                flow_cancelled=True,
            )

        flow.data["cities"] = cities
        flow.data["cities_loaded"] = True

        city_list = "\n".join(f"**{i + 1}.** {c}" for i, c in enumerate(cities))
        return FlowStepResult(
            message=(
                f"Bayilerimizin bulundugu sehirler:\n\n{city_list}\n\n"
                f"Lutfen bir sehir secin (numara veya sehir adi yazin).\n"
                f"Iptal icin **iptal** yazin."
            ),
        )

    async def _handle_city_selection(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        """Match user input to a city from the list."""
        text = user_message.strip()
        cities: list[str] = flow.data.get("cities", [])

        # Try number selection
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(cities):
                return await self._show_dealers_for_city(flow, cities[idx])
            return FlowStepResult(
                message=f"Gecersiz numara. Lutfen 1 ile {len(cities)} arasinda bir sayi girin.",
            )

        # Try name matching (fuzzy, Turkish-aware)
        text_norm = text.lower().translate(_TR_TO_ASCII)
        for city in cities:
            city_norm = city.lower().translate(_TR_TO_ASCII)
            if text_norm == city_norm or text_norm in city_norm or city_norm in text_norm:
                return await self._show_dealers_for_city(flow, city)

        return FlowStepResult(
            message="Girdiginiz sehir listede bulunamadi. Lutfen listedeki bir sehir adi veya numarasi yazin.",
        )

    async def _show_dealers_for_city(
        self, flow: ConversationFlow, city: str
    ) -> FlowStepResult:
        """Fetch and display dealers for the selected city."""
        try:
            dealers = await self._get_dealers_by_city(city)
        except Exception as e:
            logger.error("Failed to fetch dealers for city %s: %s", city, e)
            return FlowStepResult(
                message="Bayi bilgileri yuklenirken bir hata olustu. Lutfen tekrar deneyin.",
                flow_cancelled=True,
            )

        if not dealers:
            # No dealers in this city — go back to city selection
            flow.data.pop("cities_loaded", None)
            return FlowStepResult(
                message=(
                    f"**{city}** ilinde aktif bayi bulunamadi.\n"
                    f"Baska bir sehir secmek icin tekrar deneyin veya **iptal** yazin."
                ),
            )

        flow.data["selected_city"] = city
        flow.data["dealers"] = [
            {
                "id": d["id"],
                "name": d.get("name", ""),
                "phone": d.get("phone") or d.get("mobile") or "",
                "email": d.get("email") or "",
                "street": d.get("street") or "",
                "city_district": d.get("city") or "",
            }
            for d in dealers
        ]
        flow.step = "show_dealers"

        lines = []
        for i, d in enumerate(flow.data["dealers"]):
            parts = [f"**{i + 1}. {d['name']}**"]
            if d["city_district"]:
                parts.append(f"   Ilce: {d['city_district']}")
            if d["street"]:
                parts.append(f"   Adres: {d['street']}")
            if d["phone"]:
                parts.append(f"   Tel: {d['phone']}")
            lines.append("\n".join(parts))

        dealer_list = "\n\n".join(lines)
        return FlowStepResult(
            message=(
                f"**{city}** ilindeki bayilerimiz:\n\n{dealer_list}\n\n"
                f"Iletisim bilgilerinizi birakmak ister misiniz?\n"
                f"Bayi numarasini yazin veya **hayir** yazin."
            ),
        )

    async def _handle_dealer_selection(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        """User selects a dealer or declines."""
        text = user_message.strip().lower()
        dealers = flow.data.get("dealers", [])

        # User declines
        if text in ("hayir", "hayır", "no", "yok"):
            return FlowStepResult(
                message="Tamam, bayi listemizi incelediginiz icin tesekkurler! Baska bir konuda yardimci olabilir miyim?",
                flow_completed=True,
            )

        # Go back to city selection
        if text in ("geri", "back", "baska", "başka"):
            flow.step = "await_city"
            flow.data.pop("cities_loaded", None)
            return await self._load_cities(flow)

        # Number selection
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(dealers):
                flow.data["selected_dealer"] = dealers[idx]
                flow.step = "await_contact"
                return FlowStepResult(
                    message=(
                        f"**{dealers[idx]['name']}** bayisi secildi.\n\n"
                        f"Lutfen iletisim bilgilerinizi yazin:\n"
                        f"**Adiniz, telefon numaraniz ve e-posta adresiniz**\n"
                        f"Ornegin: Ali Yilmaz, 0532 123 4567, ali@firma.com"
                    ),
                )
            return FlowStepResult(
                message=f"Gecersiz numara. Lutfen 1 ile {len(dealers)} arasinda bir sayi girin.",
            )

        return FlowStepResult(
            message="Lutfen bir bayi numarasi secin veya **hayir** yazin.",
        )

    async def _handle_contact(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        """Parse contact info from user message."""
        text = user_message.strip()

        # Extract email and phone
        email_match = EMAIL_RE.search(text)
        phone_match = PHONE_RE.search(text)

        email = email_match.group(0) if email_match else ""
        phone = phone_match.group(0).strip() if phone_match else ""

        # Extract name: remove email and phone, take remaining text
        name = text
        if email:
            name = name.replace(email, "")
        if phone:
            name = name.replace(phone, "")
        # Clean up separators and whitespace
        name = re.sub(r"[,;/\-]+", " ", name).strip()
        name = re.sub(r"\s+", " ", name)

        if not name or len(name) < 2:
            return FlowStepResult(
                message=(
                    "Lutfen en az adinizi yazin.\n"
                    "Ornegin: Ali Yilmaz, 0532 123 4567, ali@firma.com"
                ),
            )

        if not email and not phone:
            return FlowStepResult(
                message=(
                    "Lutfen en az bir iletisim bilgisi girin (telefon veya e-posta).\n"
                    "Ornegin: Ali Yilmaz, 0532 123 4567, ali@firma.com"
                ),
            )

        flow.data["customer_name"] = name
        flow.data["customer_phone"] = phone
        flow.data["customer_email"] = email
        flow.step = "await_confirm"

        dealer = flow.data.get("selected_dealer", {})
        city = flow.data.get("selected_city", "")

        return FlowStepResult(
            message=(
                f"Bilgileriniz:\n"
                f"- **Ad:** {name}\n"
                f"- **Telefon:** {phone or 'Belirtilmedi'}\n"
                f"- **E-posta:** {email or 'Belirtilmedi'}\n"
                f"- **Bayi:** {dealer.get('name', '')} ({city})\n\n"
                f"Gondermek istiyor musunuz? (**evet** / **hayir**)"
            ),
        )

    async def _handle_confirm(
        self, flow: ConversationFlow, user_message: str
    ) -> FlowStepResult:
        """Create CRM lead and send email to dealer."""
        text = user_message.strip().lower()

        if text in ("hayir", "hayır", "no"):
            return FlowStepResult(
                message="Iptal edildi. Baska bir konuda yardimci olabilir miyim?",
                flow_cancelled=True,
            )

        if text not in ("evet", "yes", "ok", "tamam", "olur", "onay"):
            return FlowStepResult(
                message="Lutfen **evet** veya **hayir** yazin.",
            )

        dealer = flow.data.get("selected_dealer", {})
        name = flow.data.get("customer_name", "")
        phone = flow.data.get("customer_phone", "")
        email = flow.data.get("customer_email", "")
        city = flow.data.get("selected_city", "")

        # Create CRM lead
        lead_id = None
        try:
            lead_id = await self._create_lead(dealer, name, phone, email, city)
            if lead_id:
                logger.info("CRM lead created: id=%s, dealer=%s, customer=%s", lead_id, dealer.get("name"), name)
        except Exception as e:
            logger.error("Failed to create CRM lead: %s", e)

        # Send email to dealer
        email_sent = False
        try:
            email_sent = await asyncio.to_thread(
                self._send_dealer_notification, dealer, name, phone, email, city
            )
        except Exception as e:
            logger.error("Failed to send dealer email: %s", e)

        if not lead_id and not email_sent:
            return FlowStepResult(
                message="Talebiniz iletilirken bir hata olustu. Lutfen daha sonra tekrar deneyin.",
                flow_cancelled=True,
            )

        return FlowStepResult(
            message=(
                f"Talebiniz basariyla iletildi!\n"
                f"**{dealer.get('name', '')}** bayimiz en kisa surede sizinle iletisime gececektir.\n"
                f"Baska bir konuda yardimci olabilir miyim?"
            ),
            flow_completed=True,
            data={
                "lead_id": lead_id,
                "dealer_name": dealer.get("name"),
                "city": city,
            },
        )

    # ── Odoo queries ─────────────────────────────────────────────

    async def _get_dealer_cities(self) -> list[str]:
        """Get distinct cities (provinces) from dealer partners via state_id."""
        domain = [
            ["category_id", "in", DEALER_CATEGORY_IDS],
            ["state_id", "!=", False],
        ]
        dealers = await self.odoo.adapter.call(
            "res.partner",
            "search_read",
            [domain],
            {"fields": ["state_id"], "limit": 500},
        )
        # state_id returns [id, "Ankara (TR)"] tuples — extract city name
        cities_set: set[str] = set()
        for d in dealers:
            state = d.get("state_id")
            if state and isinstance(state, list) and len(state) > 1:
                # "Ankara (TR)" → "Ankara"
                city_name = state[1].split("(")[0].strip()
                if city_name:
                    cities_set.add(city_name)
        return sorted(cities_set)

    async def _get_dealers_by_city(self, city: str) -> list[dict]:
        """Get dealer partners for a given city/province via state_id name."""
        domain = [
            ["category_id", "in", DEALER_CATEGORY_IDS],
            ["state_id.name", "ilike", city],
            ["is_company", "=", True],
        ]
        return await self.odoo.adapter.call(
            "res.partner",
            "search_read",
            [domain],
            {
                "fields": ["id", "name", "city", "phone", "mobile", "email", "street", "website"],
                "limit": 50,
            },
        )

    async def _create_lead(
        self, dealer: dict, customer_name: str, phone: str, email: str, city: str
    ) -> int | None:
        """Create a CRM lead in Odoo assigned to the dealer."""
        vals: dict = {
            "name": f"Chatbot Bayi Talebi - {customer_name} ({city})",
            "partner_id": dealer.get("id"),
            "contact_name": customer_name,
            "email_from": email or False,
            "phone": phone or False,
            "city": city,
            "description": (
                f"Chatbot uzerinden bayi bulma talebi.\n"
                f"Musteri: {customer_name}\n"
                f"Telefon: {phone or 'Belirtilmedi'}\n"
                f"E-posta: {email or 'Belirtilmedi'}\n"
                f"Sehir: {city}\n"
                f"Secilen Bayi: {dealer.get('name', '')}"
            ),
            "type": "lead",
        }

        # Try to set utm source to "Chatbot" if exists
        try:
            sources = await self.odoo.adapter.call(
                "utm.source",
                "search_read",
                [[["name", "ilike", "chatbot"]]],
                {"fields": ["id"], "limit": 1},
            )
            if sources:
                vals["source_id"] = sources[0]["id"]
        except Exception:
            pass

        lead_id = await self.odoo.adapter.call("crm.lead", "create", [vals])
        return lead_id

    def _send_dealer_notification(
        self, dealer: dict, customer_name: str, phone: str, email: str, city: str
    ) -> bool:
        """Send notification email to the dealer (synchronous, called via to_thread)."""
        dealer_email = dealer.get("email")
        if not dealer_email:
            logger.warning("Dealer %s has no email, skipping notification", dealer.get("name"))
            return False

        body_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; padding: 20px;">
            <div style="text-align: center; margin-bottom: 20px;">
                <h2 style="color: #231f20; margin: 0;">ID Fine</h2>
                <p style="color: #666; margin: 5px 0;">Yeni Musteri Talebi</p>
            </div>
            <div style="background: #f7f7f7; border-radius: 8px; padding: 24px;">
                <p><strong>Musteri Adi:</strong> {customer_name}</p>
                <p><strong>Telefon:</strong> {phone or 'Belirtilmedi'}</p>
                <p><strong>E-posta:</strong> {email or 'Belirtilmedi'}</p>
                <p><strong>Sehir:</strong> {city}</p>
            </div>
            <p style="color: #999; font-size: 11px; text-align: center; margin-top: 20px;">
                Bu talep ID Fine chatbot uzerinden otomatik olusturulmustur.
                Lutfen musteriye en kisa surede donus yapiniz.
            </p>
        </div>
        """

        return self.email.send(
            to=dealer_email,
            subject=f"Yeni Musteri Talebi - {customer_name} ({city})",
            body_html=body_html,
        )
