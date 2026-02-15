import logging
from typing import AsyncGenerator

import anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SYSTEM_PROMPT = """Sen ID Fine (Porser Porselen) firmasinin AI musteri destek asistanisin. ID Fine, Turkiye'de HORECA sektorunde porselen uretim ve satis yapan bir markadir.

Markalar: ID Fine, 1972, Roots
Materyaller: Fine China, Fine Stoneware, Stoneware

KURALLAR:
1. SADECE sana verilen context bilgisiyle yanit ver. Context'te olmayan bilgiyi uydurma.
2. Eger soruyu context ile cevaplayamiyorsan, kibarca bilgi bulunamadigini belirt ve musteri hizmetlerini ara demesini oner.
3. Kullanicinin mesajini HANGI DILDE yazdiysa, AYNI DILDE yanit ver. Turkce soru -> Turkce yanit. English question -> English answer. Varsayilan dil Turkce'dir.
4. Fiyat veya stok bilgisi verirken bunun anlik oldugunu belirt.
5. Urun onerisi yaparken HORECA sektorune uygun oneriler sun.
6. Kisa ve oz yanitlar ver, gereksiz uzatma.
7. Yanitlarini Markdown formatinda yaz. Basliklar icin **kalin**, listeler icin - veya 1. kullan.
8. <urun_veritabani> bolumunde urun kodu, koleksiyon, marka, tip, ebat, hacim, materyal, renk, stil, fiyat, stok durumu, servis tipi, yemek onerileri ve dayanim bilgileri bulunabilir. Urun bilgisinde "Resim:" alani varsa, urun bilgilerini sunduktan sonra resmi markdown formatinda goster: ![Urun Adi](resim_url) seklinde. Resim URL'sini oldugu gibi kullan, degistirme.
9. Kullanici yemek veya mutfak turu sordugunda, uygun servis tipi ve tabak onerilerini sun.
10. <musteri_verileri> bolumunde dogrulanmis musterinin siparis, fatura, odeme, teslimat, profil veya destek talebi verileri bulunabilir. Bu verileri kullaniciya anlasilir ve duzenli sekilde sun.
11. Siparis durumlari: Taslak, Gonderildi, Onaylandi, Tamamlandi, Iptal. Fatura durumlari: Taslak, Kesildi, Iptal. Odeme durumlari: Odendi, Odenmedi, Kismi Odendi.
12. Musteri verileri gosterirken tutarlari TRY formatinda goster (ornek: 1.250,00 TRY)."""


class LLMService:
    def __init__(self):
        self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def generate(
        self,
        user_message: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        product_data: str = "",
        customer_data: str = "",
    ) -> str:
        """Generate a non-streaming response."""
        messages = self._build_messages(
            user_message, context, conversation_history, product_data, customer_data
        )

        response = await self.client.messages.create(
            model=settings.claude_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        return response.content[0].text

    async def generate_stream(
        self,
        user_message: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        product_data: str = "",
        customer_data: str = "",
    ) -> AsyncGenerator[str, None]:
        """Generate a streaming response, yielding text chunks."""
        messages = self._build_messages(
            user_message, context, conversation_history, product_data, customer_data
        )

        async with self.client.messages.stream(
            model=settings.claude_model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def classify_intent(self, message: str) -> str:
        """Classify user intent using a fast model."""
        classification_prompt = """Kullanıcının mesajını aşağıdaki kategorilerden birine sınıflandır.
Sadece kategori adını döndür, başka bir şey yazma.

Kategoriler:
- PRODUCT_INFO: Ürün özellikleri, malzeme, boyut, koleksiyon bilgisi
- PRICE_INQUIRY: Fiyat sorgusu
- STOCK_CHECK: Stok durumu sorgusu
- QUOTE_REQUEST: Teklif isteme
- GENERAL_INFO: Firma, marka, sektör hakkında genel bilgi, selamlaşma
- HYBRID: Hem ürün bilgisi hem fiyat/stok birlikte
- OUT_OF_SCOPE: İdfine/Porser ile hiç ilgisi olmayan konular
- ORDER_HISTORY: Siparişlerimi göster, geçmiş siparişler
- ORDER_DETAIL: Sipariş detayı, belirli sipariş bilgisi
- ORDER_CREATE: Sipariş vermek istiyorum, satın alma
- ORDER_CANCEL: Sipariş iptal, iade
- INVOICE_LIST: Faturalarım, hesap özeti
- INVOICE_DOWNLOAD: Fatura indir, PDF fatura
- PAYMENT_HISTORY: Ödeme durumu, ödeme geçmişi, borç, bakiye
- DELIVERY_TRACKING: Kargo takip, teslimat durumu, ne zaman gelecek
- PROFILE_VIEW: Profilim, hesap bilgilerim
- PROFILE_UPDATE: Telefon/adres/email güncelleme
- SUPPORT_TICKET_CREATE: Destek talebi, sorun bildirmek
- SUPPORT_TICKET_LIST: Taleplerim, ticket'larım
- SPENDING_REPORT: Harcama raporu, istatistik
- CUSTOMER_AUTH: Giriş yap, kimlik doğrula
- CUSTOMER_LOGOUT: Çıkış yap

ÖNEMLİ: Selamlaşma, teşekkür, hoşça kal → GENERAL_INFO. Sipariş/fatura/kargo/profil gibi kişisel müşteri sorguları ilgili kategoriye.

Kullanıcı mesajı: """

        response = await self.client.messages.create(
            model=settings.claude_classifier_model,
            max_tokens=30,
            messages=[
                {"role": "user", "content": classification_prompt + message}
            ],
        )

        intent = response.content[0].text.strip().upper().replace(" ", "_")

        valid_intents = {
            "PRODUCT_INFO", "PRICE_INQUIRY", "STOCK_CHECK",
            "ORDER_STATUS", "QUOTE_REQUEST", "GENERAL_INFO",
            "HYBRID", "OUT_OF_SCOPE",
            "ORDER_HISTORY", "ORDER_DETAIL", "ORDER_CREATE", "ORDER_CANCEL",
            "INVOICE_LIST", "INVOICE_DETAIL", "INVOICE_DOWNLOAD",
            "PAYMENT_STATUS", "PAYMENT_HISTORY",
            "DELIVERY_TRACKING",
            "PROFILE_VIEW", "PROFILE_UPDATE", "ADDRESS_UPDATE",
            "SUPPORT_TICKET_CREATE", "SUPPORT_TICKET_LIST",
            "CATALOG_REQUEST", "SPENDING_REPORT",
            "CUSTOMER_AUTH", "CUSTOMER_LOGOUT",
        }

        if intent not in valid_intents:
            return "GENERAL_INFO"

        return intent

    def _build_messages(
        self,
        user_message: str,
        context: str,
        conversation_history: list[dict] | None,
        product_data: str,
        customer_data: str = "",
    ) -> list[dict]:
        messages = []

        # Add conversation history (last 10 messages)
        if conversation_history:
            for msg in conversation_history[-10:]:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"],
                })

        # Build the current message with context
        parts = []

        if context:
            parts.append(f"<bilgi_kaynaklari>\n{context}\n</bilgi_kaynaklari>")

        if product_data:
            parts.append(f"<urun_veritabani>\n{product_data}\n</urun_veritabani>")

        if customer_data:
            parts.append(customer_data)

        parts.append(f"<kullanici_sorusu>\n{user_message}\n</kullanici_sorusu>")

        messages.append({"role": "user", "content": "\n\n".join(parts)})

        return messages
