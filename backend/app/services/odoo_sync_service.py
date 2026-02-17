"""Odoo → PostgreSQL product sync service.

Periodically fetches product prices and stock levels from Odoo ERP
and upserts them into the local products table.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import async_session
from app.models.audit import OdooSyncLog
from app.models.product import Product
from app.odoo.base_adapter import OdooAdapter
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)
settings = get_settings()


class OdooSyncService:
    """Synchronises Odoo product data to the local PostgreSQL database."""

    def __init__(self, adapter: OdooAdapter, cache: CacheService):
        self.adapter = adapter
        self.cache = cache

    # ------------------------------------------------------------------
    # Public entry points (called by scheduler / manual trigger)
    # ------------------------------------------------------------------

    async def delta_sync(self):
        """Incremental sync — only products modified since the last sync."""
        log = await self._start_log("delta")
        try:
            last_write_date = await self._get_last_write_date()
            logger.info("Delta sync: fetching products changed after %s", last_write_date or "never")

            domain = [["active", "in", [True, False]]]
            if last_write_date:
                domain.append(["write_date", ">", last_write_date])

            products = await self._fetch_products(domain)
            if not products:
                logger.info("Delta sync: no changed products found")
                await self._finish_log(log, 0)
                return

            stock_map = await self._fetch_stock([p["id"] for p in products])
            count = await self._upsert_products(products, stock_map)
            await self._invalidate_cache()
            await self._finish_log(log, count)
            logger.info("Delta sync: upserted %d products", count)

        except Exception as e:
            logger.exception("Delta sync failed")
            await self._fail_log(log, str(e))
            raise

    async def full_sync(self):
        """Full sync — fetch all products, deactivate removed ones."""
        log = await self._start_log("full")
        try:
            offset = 0
            all_odoo_ids: set[int] = set()
            total_upserted = 0
            batch_size = settings.odoo_sync_batch_size

            while True:
                domain = [["active", "in", [True, False]]]
                products = await self._fetch_products(domain, limit=batch_size, offset=offset)
                if not products:
                    break

                ids = [p["id"] for p in products]
                all_odoo_ids.update(ids)
                stock_map = await self._fetch_stock(ids)
                total_upserted += await self._upsert_products(products, stock_map)
                offset += batch_size

                if len(products) < batch_size:
                    break

            # Deactivate products no longer in Odoo
            deactivated = await self._deactivate_missing(all_odoo_ids)
            if deactivated:
                logger.info("Full sync: deactivated %d products not found in Odoo", deactivated)

            await self._invalidate_cache()
            await self._finish_log(log, total_upserted)
            logger.info("Full sync: upserted %d products total", total_upserted)

        except Exception as e:
            logger.exception("Full sync failed")
            await self._fail_log(log, str(e))
            raise

    # ------------------------------------------------------------------
    # Odoo data fetching
    # ------------------------------------------------------------------

    async def _fetch_products(
        self, domain: list, limit: int = 0, offset: int = 0
    ) -> list[dict]:
        """Fetch products from Odoo via JSON-RPC."""
        fields = [
            "id", "name", "default_code", "list_price",
            "active", "write_date", "categ_id",
        ]
        kwargs: dict = {"fields": fields, "order": "id asc"}
        if limit:
            kwargs["limit"] = limit
        if offset:
            kwargs["offset"] = offset

        return await self.adapter.call(
            "product.product", "search_read", [domain], kwargs
        )

    async def _fetch_stock(self, product_ids: list[int]) -> dict[int, float]:
        """Fetch aggregated stock for a list of product IDs."""
        if not product_ids:
            return {}

        records = await self.adapter.call(
            "stock.quant",
            "search_read",
            [[["product_id", "in", product_ids]]],
            {"fields": ["product_id", "quantity"]},
        )

        stock_map: dict[int, float] = {}
        for r in records:
            pid = r["product_id"][0] if isinstance(r["product_id"], list) else r["product_id"]
            stock_map[pid] = stock_map.get(pid, 0) + (r.get("quantity") or 0)
        return stock_map

    # ------------------------------------------------------------------
    # Database upsert
    # ------------------------------------------------------------------

    async def _upsert_products(
        self, odoo_products: list[dict], stock_map: dict[int, float]
    ) -> int:
        """Upsert Odoo products into local DB. Returns count of affected rows."""
        now = datetime.now(timezone.utc)
        count = 0

        # Deduplicate by default_code — keep last variant per code
        # and aggregate stock across all variants with the same code
        code_map: dict[str, dict] = {}
        code_stock: dict[str, float] = {}
        for rec in odoo_products:
            default_code = (rec.get("default_code") or "").strip()
            if not default_code:
                continue
            code_map[default_code] = rec
            odoo_id = rec["id"]
            code_stock[default_code] = code_stock.get(default_code, 0) + stock_map.get(odoo_id, 0)

        async with async_session() as db:
            for default_code, rec in code_map.items():
                odoo_id = rec["id"]
                raw_stock = code_stock.get(default_code, 0)
                # Clamp to int32 range; treat extreme negatives as 0
                stock_qty = max(0, min(int(raw_stock), 2_147_483_647))

                # Try to find existing product by odoo_product_id first, then by urun_kodu
                product = await self._find_product(db, odoo_id, default_code)

                if product:
                    # Update price, stock, active, sync metadata
                    product.fiyat = rec.get("list_price") or 0
                    product.stok = stock_qty
                    product.aktif = bool(rec.get("active", True))
                    product.odoo_product_id = odoo_id
                    product.odoo_write_date = rec.get("write_date")
                    product.last_synced_at = now
                else:
                    # Create minimal new product
                    product = Product(
                        urun_kodu=default_code,
                        urun_tanimi=rec.get("name"),
                        fiyat=rec.get("list_price") or 0,
                        stok=stock_qty,
                        aktif=bool(rec.get("active", True)),
                        odoo_product_id=odoo_id,
                        odoo_write_date=rec.get("write_date"),
                        last_synced_at=now,
                    )
                    db.add(product)

                count += 1

            await db.commit()

        return count

    async def _find_product(
        self, db: AsyncSession, odoo_id: int, default_code: str
    ) -> Product | None:
        """Find a product by odoo_product_id or fallback to urun_kodu."""
        # First try by Odoo ID (fast, indexed)
        stmt = select(Product).where(Product.odoo_product_id == odoo_id).limit(1)
        result = await db.execute(stmt)
        product = result.scalar_one_or_none()
        if product:
            return product

        # Fallback: match by product code
        stmt = select(Product).where(Product.urun_kodu == default_code).limit(1)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _deactivate_missing(self, odoo_ids: set[int]) -> int:
        """Set aktif=False for products with odoo_product_id not in the given set."""
        if not odoo_ids:
            return 0

        async with async_session() as db:
            stmt = (
                select(Product)
                .where(
                    Product.odoo_product_id.isnot(None),
                    Product.odoo_product_id.notin_(odoo_ids),
                    Product.aktif == True,
                )
            )
            result = await db.execute(stmt)
            products = result.scalars().all()

            for p in products:
                p.aktif = False

            await db.commit()
            return len(products)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_last_write_date(self) -> str | None:
        """Get the latest odoo_write_date from the local products table."""
        async with async_session() as db:
            stmt = select(func.max(Product.odoo_write_date))
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def _invalidate_cache(self):
        """Clear all Odoo-related Redis caches after a successful sync."""
        try:
            await self.cache.delete_pattern("odoo:products:*")
            await self.cache.delete_pattern("odoo:price:*")
            await self.cache.delete_pattern("odoo:stock:*")
            logger.info("Sync: Redis cache invalidated")
        except Exception:
            logger.warning("Sync: failed to invalidate Redis cache", exc_info=True)

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    async def _start_log(self, sync_type: str) -> int:
        async with async_session() as db:
            log = OdooSyncLog(
                sync_type=sync_type,
                status="running",
                started_at=datetime.now(timezone.utc),
            )
            db.add(log)
            await db.commit()
            await db.refresh(log)
            return log.id

    async def _finish_log(self, log_id: int, records_synced: int):
        async with async_session() as db:
            stmt = select(OdooSyncLog).where(OdooSyncLog.id == log_id)
            result = await db.execute(stmt)
            log = result.scalar_one_or_none()
            if log:
                log.status = "success"
                log.records_synced = records_synced
                log.completed_at = datetime.now(timezone.utc)
                await db.commit()

    async def _fail_log(self, log_id: int, error_message: str):
        async with async_session() as db:
            stmt = select(OdooSyncLog).where(OdooSyncLog.id == log_id)
            result = await db.execute(stmt)
            log = result.scalar_one_or_none()
            if log:
                log.status = "failure"
                log.error_message = error_message[:2000]
                log.completed_at = datetime.now(timezone.utc)
                await db.commit()
