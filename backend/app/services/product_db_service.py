"""Product database service - queries products from PostgreSQL for chat context."""

import logging
import re

from sqlalchemy import and_, func, or_, select, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product

logger = logging.getLogger(__name__)


# ASCII ↔ Turkish character mapping for normalization
_TR_TO_ASCII = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
_ASCII_TO_TR = {"c": "ç", "g": "ğ", "i": "ı", "o": "ö", "s": "ş", "u": "ü"}


class ProductDBService:
    """Searches local product database and formats results for LLM context."""

    def __init__(self, db: AsyncSession):
        self.db = db

    def _keyword_condition(self, kw: str):
        """Build OR condition for a keyword across all searchable columns, including Turkish variants."""
        variants = self._turkish_variants(kw)
        cols = [
            Product.urun_tanimi, Product.koleksiyon, Product.urun_tipi,
            Product.marka, Product.model, Product.ana_renk, Product.materyal,
            Product.servis_tipi, Product.mutfak_uyumu, Product.yemek_onerileri,
            Product.konsept_etiketler, Product.urun_kodu, Product.dekor,
            Product.stil,
        ]
        parts = []
        for v in variants:
            like = f"%{v}%"
            for col in cols:
                parts.append(col.ilike(like))
        return or_(*parts)

    async def search_products(self, query: str, limit: int = 10) -> list[dict]:
        """Search products by keyword matching. Tries AND first, falls back to OR."""
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        kw_conditions = [self._keyword_condition(kw) for kw in keywords]

        # Try AND first (all keywords must match)
        stmt = (
            select(Product)
            .where(and_(Product.aktif == True, *kw_conditions))
            .order_by(Product.fiyat.desc().nullslast())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        products = result.scalars().all()

        # Fallback to OR if AND returns nothing
        if not products and len(keywords) > 1:
            stmt = (
                select(Product)
                .where(and_(Product.aktif == True, or_(*kw_conditions)))
                .order_by(Product.fiyat.desc().nullslast())
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            products = result.scalars().all()

        return [self._product_to_dict(p) for p in products]

    async def get_products_by_type(self, urun_tipi: str, limit: int = 10) -> list[dict]:
        """Get products filtered by product type."""
        stmt = (
            select(Product)
            .where(and_(Product.aktif == True, Product.urun_tipi.ilike(f"%{urun_tipi}%")))
            .order_by(Product.koleksiyon, Product.ebat_cm)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [self._product_to_dict(p) for p in result.scalars().all()]

    async def get_products_by_color(self, renk: str, limit: int = 10) -> list[dict]:
        """Get products filtered by color."""
        stmt = (
            select(Product)
            .where(and_(Product.aktif == True, Product.ana_renk.ilike(f"%{renk}%")))
            .order_by(Product.koleksiyon, Product.urun_tipi)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [self._product_to_dict(p) for p in result.scalars().all()]

    async def get_products_by_collection(self, koleksiyon: str, limit: int = 15) -> list[dict]:
        """Get all products in a collection."""
        stmt = (
            select(Product)
            .where(and_(Product.aktif == True, Product.koleksiyon.ilike(f"%{koleksiyon}%")))
            .order_by(Product.urun_tipi, Product.ebat_cm)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        return [self._product_to_dict(p) for p in result.scalars().all()]

    async def get_product_price(self, query: str, limit: int = 10) -> list[dict]:
        """Search products with price info."""
        return await self._search_with_fallback(query, limit, Product.fiyat.desc().nullslast())

    async def get_stock_info(self, query: str, limit: int = 10) -> list[dict]:
        """Search products with stock info."""
        return await self._search_with_fallback(query, limit, Product.stok.desc())

    async def _search_with_fallback(self, query: str, limit: int, order_by) -> list[dict]:
        """Search with AND first, fallback to OR."""
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        kw_conditions = [self._keyword_condition(kw) for kw in keywords]

        stmt = (
            select(Product)
            .where(and_(Product.aktif == True, *kw_conditions))
            .order_by(order_by)
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        products = result.scalars().all()

        if not products and len(keywords) > 1:
            stmt = (
                select(Product)
                .where(and_(Product.aktif == True, or_(*kw_conditions)))
                .order_by(order_by)
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            products = result.scalars().all()

        return [self._product_to_dict(p) for p in products]

    async def recommend_by_food(self, food_query: str, limit: int = 10) -> list[dict]:
        """Recommend products based on food/cuisine type."""
        keywords = self._extract_keywords(food_query)
        if not keywords:
            return []

        kw_conditions = [self._keyword_condition(kw) for kw in keywords]
        combined = and_(*kw_conditions) if len(keywords) <= 2 else or_(*kw_conditions)

        stmt = (
            select(Product)
            .where(and_(Product.aktif == True, combined))
            .order_by(Product.koleksiyon, Product.urun_tipi)
            .limit(limit)
        )

        result = await self.db.execute(stmt)
        return [self._product_to_dict(p) for p in result.scalars().all()]

    def format_products_context(self, products: list[dict], pricelist_info: dict | None = None) -> str:
        """Format product list into text context for LLM.

        If pricelist_info is provided (authenticated customer), applies their
        discount to the base price.
        """
        if not products:
            return ""

        discount = 0.0
        pricelist_name = ""
        if pricelist_info:
            discount = pricelist_info.get("discount_percent", 0)
            pricelist_name = pricelist_info.get("pricelist_name", "")

        lines = []
        for p in products:
            parts = [f"Ürün: {p['urun_tanimi']}"]
            parts.append(f"  Kod: {p['urun_kodu']}")
            if p.get('koleksiyon'):
                parts.append(f"  Koleksiyon: {p['koleksiyon']}")
            if p.get('marka'):
                parts.append(f"  Marka: {p['marka']}")
            if p.get('urun_tipi'):
                parts.append(f"  Tip: {p['urun_tipi']}")
            if p.get('ebat_cm'):
                parts.append(f"  Ebat: {p['ebat_cm']} cm")
            if p.get('hacim_cc'):
                parts.append(f"  Hacim: {p['hacim_cc']} cc")
            if p.get('materyal'):
                parts.append(f"  Materyal: {p['materyal']}")
            if p.get('ana_renk'):
                parts.append(f"  Renk: {p['ana_renk']}")
            if p.get('fiyat') and float(p['fiyat']) > 0:
                base_price = float(p['fiyat'])
                currency = p.get('para_birimi', 'TRY')
                if discount > 0:
                    customer_price = base_price * (1 - discount / 100)
                    parts.append(f"  Fiyat: {customer_price:,.2f} {currency} ({pricelist_name})")
                else:
                    parts.append(f"  Fiyat: {p['fiyat']} {currency}")
            if p.get('stok') is not None:
                stok_val = p['stok']
                if stok_val > 0:
                    parts.append(f"  Stok: {stok_val} adet")
                else:
                    parts.append("  Stok: Tükendi")
            if p.get('servis_tipi'):
                parts.append(f"  Servis Tipi: {p['servis_tipi']}")
            if p.get('yemek_onerileri'):
                parts.append(f"  Yemek Önerileri: {p['yemek_onerileri']}")
            if p.get('istiflenebilirlik'):
                parts.append(f"  İstiflenebilirlik: {p['istiflenebilirlik']}")
            if p.get('dayanim_seviyesi'):
                parts.append(f"  Dayanım: {p['dayanim_seviyesi']}")
            if p.get('image_url'):
                img = p['image_url']
                if img.startswith('/'):
                    img = f"https://idfine.codsol.fi{img}"
                parts.append(f"  Gorsel: {img}")
            lines.append("\n".join(parts))

        return "\n---\n".join(lines)

    def _product_to_dict(self, p: Product) -> dict:
        return {
            "id": p.id,
            "urun_kodu": p.urun_kodu,
            "urun_tanimi": p.urun_tanimi,
            "marka": p.marka,
            "koleksiyon": p.koleksiyon,
            "model": p.model,
            "urun_tipi": p.urun_tipi,
            "ebat_cm": p.ebat_cm,
            "hacim_cc": p.hacim_cc,
            "materyal": p.materyal,
            "ana_renk": p.ana_renk,
            "stil": p.stil,
            "fiyat": str(p.fiyat) if p.fiyat else None,
            "para_birimi": p.para_birimi,
            "stok": p.stok,
            "servis_tipi": p.servis_tipi,
            "mutfak_uyumu": p.mutfak_uyumu,
            "yemek_onerileri": p.yemek_onerileri,
            "konsept_etiketler": p.konsept_etiketler,
            "istiflenebilirlik": p.istiflenebilirlik,
            "dayanim_seviyesi": p.dayanim_seviyesi,
            "image_url": p.image,
        }

    @staticmethod
    def _turkish_variants(word: str) -> list[str]:
        """Generate both ASCII and Turkish-character variants of a word."""
        variants = {word}
        # ASCII → Turkish variant
        tr_variant = word
        for ascii_ch, tr_ch in _ASCII_TO_TR.items():
            tr_variant = tr_variant.replace(ascii_ch, tr_ch)
        variants.add(tr_variant)
        # Turkish → ASCII variant
        ascii_variant = word.translate(_TR_TO_ASCII)
        variants.add(ascii_variant)
        return list(variants)

    def _extract_keywords(self, query: str) -> list[str]:
        """Extract meaningful keywords from user query, ignoring stop words."""
        stop_words = {
            # Turkish common
            "bir", "bu", "şu", "su", "ve", "veya", "ile", "için", "icin",
            "mi", "mı", "mu", "mü", "ne", "nedir", "nasıl", "nasil",
            "kadar", "var", "yok", "lütfen", "lutfen",
            "istiyorum", "göster", "goster", "bana", "hakkında", "hakkinda",
            "bilgi", "ürünler", "urunler", "ürün", "urun", "listele",
            "öner", "oner", "tavsiye", "hangi", "hangisi", "tane", "adet",
            "kodlu", "kodunu", "kodu",
            # Turkish noun declensions & question words
            "ürünün", "urunun", "ürünü", "urunu", "ürünleri", "urunleri",
            "adı", "adi", "adını", "adini", "adın", "adin", "adlı", "adli",
            "ismi", "ismini", "modeli", "modelin",
            "resmi", "resim", "resmini", "resimler", "görseli", "gorseli",
            "misiniz", "musunuz", "mısınız", "müsünüz", "misin", "musun",
            "önerir", "onerir", "önerebilir", "onerebilir", "söyler",
            "soyler", "verir", "bakar", "eder", "olur", "olabilir",
            "renk", "rengi", "renkte", "renkli", "renkleri",
            "fiyat", "fiyatı", "fiyati", "fiyatları", "fiyatlari",
            "kaç", "kac", "nelerdir", "neler",
            "çeşitleri", "cesitleri", "çeşit", "cesit", "cesitleriniz",
            "ürünleriniz", "urunleriniz", "stok", "stokta", "stoğu", "stogu",
            "durumu", "bedeli", "tutarı", "tutari",
            # English common
            "the", "a", "an", "is", "are", "what", "which", "how", "can",
            "do", "you", "have", "show", "me", "please", "recommend",
            "price", "stock", "about", "tell", "much", "many",
        }
        # Extract product codes (e.g. AVN-CLSKS17 or 57001-163032) before cleaning
        product_codes = re.findall(r'[A-Za-z0-9]{2,}-[A-Za-z0-9]+', query)

        # Clean and split
        text = re.sub(r"[^\w\sçğıöşüÇĞİÖŞÜ]", " ", query.lower())
        words = text.split()
        # Filter stop words and very short words
        keywords = [w for w in words if w not in stop_words and len(w) >= 2]

        # Prepend intact product codes (they search better as whole codes)
        if product_codes:
            keywords = [code.upper() for code in product_codes] + keywords

        return keywords
