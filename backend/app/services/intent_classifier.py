import logging
import re
from enum import StrEnum

from app.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class Intent(StrEnum):
    # Existing intents
    PRODUCT_INFO = "PRODUCT_INFO"
    PRICE_INQUIRY = "PRICE_INQUIRY"
    STOCK_CHECK = "STOCK_CHECK"
    ORDER_STATUS = "ORDER_STATUS"
    QUOTE_REQUEST = "QUOTE_REQUEST"
    GENERAL_INFO = "GENERAL_INFO"
    HYBRID = "HYBRID"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"

    # Customer intents (requires OTP auth)
    ORDER_HISTORY = "ORDER_HISTORY"
    ORDER_DETAIL = "ORDER_DETAIL"
    ORDER_CREATE = "ORDER_CREATE"
    ORDER_CANCEL = "ORDER_CANCEL"
    INVOICE_LIST = "INVOICE_LIST"
    INVOICE_DETAIL = "INVOICE_DETAIL"
    INVOICE_DOWNLOAD = "INVOICE_DOWNLOAD"
    PAYMENT_STATUS = "PAYMENT_STATUS"
    PAYMENT_HISTORY = "PAYMENT_HISTORY"
    DELIVERY_TRACKING = "DELIVERY_TRACKING"
    PROFILE_VIEW = "PROFILE_VIEW"
    PROFILE_UPDATE = "PROFILE_UPDATE"
    ADDRESS_UPDATE = "ADDRESS_UPDATE"
    SUPPORT_TICKET_CREATE = "SUPPORT_TICKET_CREATE"
    SUPPORT_TICKET_LIST = "SUPPORT_TICKET_LIST"
    CATALOG_REQUEST = "CATALOG_REQUEST"
    SPENDING_REPORT = "SPENDING_REPORT"
    CUSTOMER_AUTH = "CUSTOMER_AUTH"
    CUSTOMER_LOGOUT = "CUSTOMER_LOGOUT"

    @property
    def needs_rag(self) -> bool:
        return self in {
            Intent.PRODUCT_INFO,
            Intent.GENERAL_INFO,
            Intent.HYBRID,
        }

    @property
    def needs_odoo(self) -> bool:
        return self in {
            Intent.PRICE_INQUIRY,
            Intent.STOCK_CHECK,
            Intent.ORDER_STATUS,
            Intent.QUOTE_REQUEST,
            Intent.HYBRID,
        }

    @property
    def requires_customer_auth(self) -> bool:
        """Intents that require OTP-verified customer session."""
        return self in {
            Intent.ORDER_HISTORY, Intent.ORDER_DETAIL, Intent.ORDER_CREATE,
            Intent.ORDER_CANCEL, Intent.INVOICE_LIST, Intent.INVOICE_DETAIL,
            Intent.INVOICE_DOWNLOAD, Intent.PAYMENT_STATUS, Intent.PAYMENT_HISTORY,
            Intent.DELIVERY_TRACKING, Intent.PROFILE_VIEW, Intent.PROFILE_UPDATE,
            Intent.ADDRESS_UPDATE, Intent.SUPPORT_TICKET_CREATE,
            Intent.SUPPORT_TICKET_LIST, Intent.SPENDING_REPORT,
            Intent.QUOTE_REQUEST,
        }

    @property
    def requires_auth(self) -> bool:
        """Legacy: intents requiring employee auth (panel only)."""
        return False  # No longer used for employee-only gating


# Keyword patterns for fast pre-filtering (avoids LLM API call)
_GREETING_PATTERNS_TR = re.compile(
    r"^(merhaba|selam|g[uü]nayd[iı]n|iyi\s*(g[uü]nler|ak[sş]amlar|geceler)"
    r"|ho[sş]\s*geldin|nas[iı]ls[iı]n|naber|sa|selam[uü]n\s*aleyk[uü]m)\s*[!?.,]*$",
    re.IGNORECASE,
)
_GREETING_PATTERNS_EN = re.compile(
    r"^(hello|hi|hey|good\s*(morning|afternoon|evening)|howdy|greetings)\s*[!?.,]*$",
    re.IGNORECASE,
)
_FAREWELL_PATTERNS_TR = re.compile(
    r"^(te[sş]ekk[uü]r(ler)?|sa[gğ]\s*ol|eyvallah|ho[sş][cç]a\s*kal|g[oö]r[uü][sş][uü]r[uü]z"
    r"|iyi\s*g[uü]nler|g[uü]le\s*g[uü]le|kendine\s*iyi\s*bak)\s*[!?.,]*$",
    re.IGNORECASE,
)
_FAREWELL_PATTERNS_EN = re.compile(
    r"^(thanks?|thank\s*you|bye|goodbye|see\s*you|take\s*care)\s*[!?.,]*$",
    re.IGNORECASE,
)
_PRICE_KEYWORDS = re.compile(
    r"\b(fiyat|[uü]cret|ka[cç]\s*(tl|lira|para)|ne\s*kadar|fiyat[iı]|pahal[iı]|ucuz|maliyet"
    r"|price|cost|how\s*much|pricing)\b",
    re.IGNORECASE,
)
_STOCK_KEYWORDS = re.compile(
    r"\b(stok|stokta|mevcut|var\s*m[iı]|kalm[iı][sş]\s*m[iı]|bulunur|temin|teslimat\s*s[uü]resi"
    r"|stock|availability|available|in\s*stock)\b",
    re.IGNORECASE,
)
_ORDER_KEYWORDS = re.compile(
    r"\b(sipari[sş]|kargo|takip|teslimat|S\d{5}|SO\d{4}|order|tracking|shipment)\b",
    re.IGNORECASE,
)
_QUOTE_KEYWORDS = re.compile(
    r"\b(teklif|fiyat\s*teklifi|toplu|toptan|indirim|anla[sş]ma|quote|quotation|bulk|wholesale)\b",
    re.IGNORECASE,
)
_PRODUCT_KEYWORDS = re.compile(
    r"\b([uü]r[uü]n|tabak|bardak|fincan|kase|porselen|bone\s*china|servis|koleksiyon"
    r"|[cç]e[sş]it|boyut|[oö]zellik|malzeme|seri|plate|cup|bowl|porcelain|collection)\b",
    re.IGNORECASE,
)

# --- Customer intent patterns ---
_ORDER_HISTORY_KEYWORDS = re.compile(
    r"\b(sipari[sş]lerim|ge[cç]mi[sş]\s*sipari[sş]|sipari[sş]\s*ge[cç]mi[sş]i|sipari[sş]\s*listesi"
    r"|my\s*orders|order\s*history|past\s*orders)\b",
    re.IGNORECASE,
)
_ORDER_DETAIL_KEYWORDS = re.compile(
    r"\b(sipari[sş]\s*detay|sipari[sş]\s*bilgi|S\d{5}\s*(durumu|detay|bilgi)"
    r"|SO\d{4,}\s*(durumu|detay|bilgi)|order\s*detail)\b",
    re.IGNORECASE,
)
_ORDER_CREATE_KEYWORDS = re.compile(
    r"\b(sipari[sş]\s*ver|sipari[sş]\s*olu[sş]tur|sipari[sş]\s*a[cç]|yeni\s*sipari[sş]"
    r"|sat[iı]n\s*al|place\s*order|create\s*order|new\s*order)\b",
    re.IGNORECASE,
)
_ORDER_CANCEL_KEYWORDS = re.compile(
    r"\b(sipari[sş]\s*iptal|iptal\s*et|iade|sipari[sş].*cancel|cancel\s*order)\b",
    re.IGNORECASE,
)
_INVOICE_KEYWORDS = re.compile(
    r"\b(fatura|faturalar[iı]m|hesap\s*[oö]zeti|fatura\s*listesi"
    r"|invoice|invoices|my\s*invoices|billing)\b",
    re.IGNORECASE,
)
_INVOICE_DOWNLOAD_KEYWORDS = re.compile(
    r"\b(fatura\s*(indir|pdf|download|g[oö]nder)|pdf\s*fatura"
    r"|download\s*invoice|invoice\s*pdf)\b",
    re.IGNORECASE,
)
_PAYMENT_KEYWORDS = re.compile(
    r"\b([oö]deme|[oö]deme\s*durumu|[oö]deme\s*ge[cç]mi[sş]i|bor[cç]|bakiye"
    r"|payment|balance|amount\s*due)\b",
    re.IGNORECASE,
)
_DELIVERY_KEYWORDS = re.compile(
    r"\b(kargo|kargo\s*takip|teslimat\s*durumu|sevk[iı]yat|g[oö]nderi|ne\s*zaman\s*gelecek"
    r"|delivery|tracking|shipment\s*status)\b",
    re.IGNORECASE,
)
_PROFILE_KEYWORDS = re.compile(
    r"\b(profil|hesab[iı]m|bilgilerim|ki[sş]isel\s*bilgi|m[uü][sş]teri\s*bilgi"
    r"|my\s*profile|my\s*account|personal\s*info)\b",
    re.IGNORECASE,
)
_PROFILE_UPDATE_KEYWORDS = re.compile(
    r"\b((telefon|adres|email|e-posta|isim|ad)\s*(g[uü]ncelle|de[gğ]i[sş]tir|d[uü]zelt)"
    r"|g[uü]ncelle.*(telefon|adres|email|e-posta)|update\s*(phone|address|email))\b",
    re.IGNORECASE,
)
_SUPPORT_KEYWORDS = re.compile(
    r"\b(destek\s*talebi|sorun\s*bildir|[sş]ikayet|ticket|destek|talep\s*olu[sş]tur"
    r"|support\s*ticket|create\s*ticket|report\s*issue)\b",
    re.IGNORECASE,
)
_SUPPORT_LIST_KEYWORDS = re.compile(
    r"\b(taleplerim|ticket.*lar[iı]m|destek.*taleplerim|my\s*tickets)\b",
    re.IGNORECASE,
)
_CATALOG_KEYWORDS = re.compile(
    r"\b(katalog|pdf\s*katalog|bro[sş][uü]r|catalog|brochure)\b",
    re.IGNORECASE,
)
_SPENDING_KEYWORDS = re.compile(
    r"\b(harcama\s*rapor|istatistik|toplam\s*harcama|spending\s*report|purchase\s*summary)\b",
    re.IGNORECASE,
)
_AUTH_KEYWORDS = re.compile(
    r"\b(giri[sş]\s*yap|kimlik\s*do[gğ]rula|oturum\s*a[cç]|login|authenticate|sign\s*in)\b",
    re.IGNORECASE,
)
_LOGOUT_KEYWORDS = re.compile(
    r"\b([cç][iı]k[iı][sş]\s*yap|oturum\s*kapat|logout|sign\s*out|[cç][iı]k[iı][sş])\b",
    re.IGNORECASE,
)


class IntentClassifier:
    def __init__(self, llm_service: LLMService):
        self.llm = llm_service
        self.is_greeting = False  # Set by keyword pre-filter
        self.greeting_lang = "tr"  # Detected language for greeting responses

    async def classify(self, message: str) -> Intent:
        self.is_greeting = False
        self.greeting_lang = "tr"
        # Fast keyword pre-filter: skip LLM call for obvious intents
        fast_result = self._keyword_classify(message)
        if fast_result is not None:
            logger.info("Intent classified via keyword: %s (lang=%s)", fast_result.value, self.greeting_lang)
            return fast_result

        # Fallback to LLM classification
        raw = await self.llm.classify_intent(message)
        try:
            return Intent(raw)
        except ValueError:
            return Intent.GENERAL_INFO

    def _keyword_classify(self, message: str) -> Intent | None:
        """Fast keyword-based classification. Returns None if uncertain."""
        text = message.strip()

        # Short greetings / farewells - mark for fast-path in chat_service
        if _GREETING_PATTERNS_TR.match(text) or _FAREWELL_PATTERNS_TR.match(text):
            self.is_greeting = True
            self.greeting_lang = "tr"
            return Intent.GENERAL_INFO
        if _GREETING_PATTERNS_EN.match(text) or _FAREWELL_PATTERNS_EN.match(text):
            self.is_greeting = True
            self.greeting_lang = "en"
            return Intent.GENERAL_INFO

        # --- Customer intent patterns (checked first, more specific) ---

        # Auth / Logout
        if _LOGOUT_KEYWORDS.search(text):
            return Intent.CUSTOMER_LOGOUT
        if _AUTH_KEYWORDS.search(text):
            return Intent.CUSTOMER_AUTH

        # Order-related customer intents
        if _ORDER_CANCEL_KEYWORDS.search(text):
            return Intent.ORDER_CANCEL
        if _ORDER_CREATE_KEYWORDS.search(text):
            return Intent.ORDER_CREATE
        if _ORDER_DETAIL_KEYWORDS.search(text):
            return Intent.ORDER_DETAIL
        if _ORDER_HISTORY_KEYWORDS.search(text):
            return Intent.ORDER_HISTORY

        # Invoice / Payment
        if _INVOICE_DOWNLOAD_KEYWORDS.search(text):
            return Intent.INVOICE_DOWNLOAD
        if _INVOICE_KEYWORDS.search(text):
            return Intent.INVOICE_LIST
        if _PAYMENT_KEYWORDS.search(text):
            return Intent.PAYMENT_HISTORY

        # Delivery
        if _DELIVERY_KEYWORDS.search(text):
            return Intent.DELIVERY_TRACKING

        # Profile
        if _PROFILE_UPDATE_KEYWORDS.search(text):
            return Intent.PROFILE_UPDATE
        if _PROFILE_KEYWORDS.search(text):
            return Intent.PROFILE_VIEW

        # Support
        if _SUPPORT_LIST_KEYWORDS.search(text):
            return Intent.SUPPORT_TICKET_LIST
        if _SUPPORT_KEYWORDS.search(text):
            return Intent.SUPPORT_TICKET_CREATE

        # Catalog & Reports
        if _CATALOG_KEYWORDS.search(text):
            return Intent.CATALOG_REQUEST
        if _SPENDING_KEYWORDS.search(text):
            return Intent.SPENDING_REPORT

        # --- Original product/price/stock patterns ---
        has_price = bool(_PRICE_KEYWORDS.search(text))
        has_stock = bool(_STOCK_KEYWORDS.search(text))
        has_product = bool(_PRODUCT_KEYWORDS.search(text))

        if has_price and has_stock:
            return Intent.HYBRID
        if has_price and has_product:
            return Intent.HYBRID

        # General order keyword (without specific customer pattern)
        if _ORDER_KEYWORDS.search(text):
            return Intent.ORDER_HISTORY

        # Quote request
        if _QUOTE_KEYWORDS.search(text):
            return Intent.QUOTE_REQUEST

        # Pure price or stock
        if has_price:
            return Intent.PRICE_INQUIRY
        if has_stock:
            return Intent.STOCK_CHECK

        # Product info (RAG-only)
        if has_product:
            return Intent.PRODUCT_INFO

        # Can't determine from keywords alone → let LLM decide
        return None
