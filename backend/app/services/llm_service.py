import logging
from typing import AsyncGenerator

import anthropic

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

SYSTEM_PROMPT = """Sen ID Fine (Porser Porselen) firmasinin AI musteri destek asistanisin. ID Fine, Turkiye'de HORECA sektorunde porselen uretim ve satis yapan bir markadir.

Markalar: ID Fine, 1972, Roots
Materyaller: Porselen, Ince Seramik, Seramik

KURALLAR:
1. SADECE sana verilen context bilgisiyle yanit ver. Context'te olmayan bilgiyi uydurma.
2. Eger soruyu context ile cevaplayamiyorsan, kibarca bilgi bulunamadigini belirt ve musteri hizmetlerini ara demesini oner.
3. Kullanicinin mesajini HANGI DILDE yazdiysa, AYNI DILDE yanit ver. Turkce soru -> Turkce yanit. English question -> English answer. Varsayilan dil Turkce'dir.
4. Fiyat veya stok bilgisi verirken bunun anlik oldugunu belirt.
5. Urun onerisi yaparken HORECA sektorune uygun oneriler sun.
6. Kisa ve oz yanitlar ver, gereksiz uzatma.
7. Yanitlarini Markdown formatinda yaz. Basliklar icin **kalin**, listeler icin - veya 1. kullan.
8. <urun_veritabani> bolumunde urun kodu, koleksiyon, marka, tip, ebat, hacim, materyal, renk, stil, fiyat, stok durumu, servis tipi, yemek onerileri ve dayanim bilgileri bulunabilir. ZORUNLU: Urun bilgisinde "Gorsel:" satiri varsa, urun bilgilerini verdikten HEMEN SONRA resmi mutlaka markdown formatinda goster: ![Urun Adi](gorsel_url) - bu satirla basa. URL'yi aynen kullan, degistirme. Gorsel yoksa resim gosterme. Kullanici bir urunun fotografini veya gorselini sorduğunda da ayni kuralı uygula.
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

    # Known menu_ana_baslik categories used in the product DB
    _MENU_CATEGORIES = [
        "Çorbalar", "Tatlılar", "Et Yemekleri", "Balık & Deniz Ürünleri",
        "Tavuk Yemekleri", "Pizza & Hamur İşleri", "Kahvaltı & Brunch",
        "Başlangıçlar", "Salatalar", "Bowl", "Makarna", "Noodle", "Pilav",
    ]

    async def classify_food_category(self, query: str) -> str | None:
        """Detect if the query references a food/dish and return its menu category.

        Returns one of _MENU_CATEGORIES or None if no food is detected.
        Uses the fast classifier model to keep latency/cost low.
        """
        categories = ", ".join(self._MENU_CATEGORIES)
        prompt = (
            f"Aşağıdaki sorguda belirtilen yemek veya yiyecek hangisini kategorisine girer?\n"
            f"Kategoriler: {categories}\n"
            f"Sadece kategori adını yaz. Eğer sorguda belirli bir yemek/yiyecek adı yoksa veya "
            f"hiçbir kategoriye girmiyorsa sadece 'YOK' yaz. Başka hiçbir şey yazma.\n\n"
            f"Sorgu: {query}"
        )
        try:
            response = await self.client.messages.create(
                model=settings.claude_classifier_model,
                max_tokens=20,
                messages=[{"role": "user", "content": prompt}],
            )
            result = response.content[0].text.strip()
            if result == "YOK":
                return None
            # Validate it's a known category (allow partial match)
            for cat in self._MENU_CATEGORIES:
                if cat.lower() in result.lower() or result.lower() in cat.lower():
                    return cat
        except Exception as e:
            logger.warning("classify_food_category failed: %s", e)
        return None

    async def classify_intent(self, message: str) -> str:
        """Classify user intent using a fast model."""
        classification_prompt = """Kullanıcının mesajını aşağıdaki kategorilerden birine sınıflandır.
Sadece kategori adını döndür, başka bir şey yazma.

Kategoriler:
- PRODUCT_INFO: Ürün özellikleri, malzeme, boyut, koleksiyon, renk, tip bilgisi; ürün kodu/stok kodu/referans numarası ile ürün arama veya ürün hakkında genel bilgi isteme
- PRICE_INQUIRY: Fiyat sorgusu (ne kadar, fiyatı nedir, fiyat listesi)
- STOCK_CHECK: Stok DURUMU sorgusu — ürünün var mı yok mu, kaç adet kaldı (NOT: "stok kodu" ile ürün arayanlar PRODUCT_INFO)
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
- FIND_DEALER: Bayi bulmak, satış noktası aramak, yakınımdaki bayi
- CUSTOMER_AUTH: Giriş yap, kimlik doğrula
- CUSTOMER_LOGOUT: Çıkış yap

ÖNEMLİ:
- Selamlaşma, teşekkür, hoşça kal → GENERAL_INFO
- Sipariş/fatura/kargo/profil gibi kişisel müşteri sorguları ilgili kategoriye
- "stok kodu", "ürün kodu", "referans no" ile ürün BİLGİSİ isteyen → PRODUCT_INFO (stok sorgusu değil)
- Fiyat kelimesi geçmiyorsa PRICE_INQUIRY seçme

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
            "FIND_DEALER",
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
