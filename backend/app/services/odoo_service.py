import logging
from typing import Optional

from app.config import get_settings
from app.odoo.base_adapter import OdooAdapter
from app.odoo.json2_adapter import Json2Adapter
from app.odoo.jsonrpc_adapter import JsonRpcAdapter
from app.schemas.odoo import (
    DeliveryDetail,
    DeliverySummary,
    InvoiceDetail,
    InvoiceSummary,
    OrderDetail,
    OrderStatusInfo,
    OrderSummary,
    PartnerInfo,
    PaymentInfo,
    PriceInfo,
    ProductInfo,
    QuotationResponse,
    SpendingReport,
    StockInfo,
    TicketSummary,
)
from app.services.cache_service import CacheService

logger = logging.getLogger(__name__)
settings = get_settings()


def create_odoo_adapter() -> OdooAdapter:
    """Create the appropriate Odoo adapter based on configured version."""
    common_kwargs = {
        "url": settings.odoo_url,
        "db": settings.odoo_db,
        "username": settings.odoo_username,
        "password": settings.odoo_password,
        "api_key": settings.odoo_api_key,
        "verify_ssl": settings.odoo_verify_ssl,
    }

    if settings.odoo_version >= 19:
        return Json2Adapter(**common_kwargs)
    else:
        return JsonRpcAdapter(**common_kwargs)


class OdooService:
    """High-level Odoo service with caching."""

    def __init__(self, adapter: OdooAdapter, cache: CacheService):
        self.adapter = adapter
        self.cache = cache

    async def search_products(self, query: str, limit: int = 20) -> list[ProductInfo]:
        cached = await self.cache.get_products(query)
        if cached:
            return [ProductInfo(**p) for p in cached]

        products = await self.adapter.search_products(query, limit)
        await self.cache.set_products(
            query, [p.model_dump() for p in products]
        )
        return products

    async def get_stock(
        self, product_ids: list[int], warehouse_id: Optional[int] = None
    ) -> list[StockInfo]:
        # Check cache for each product
        uncached_ids = []
        cached_results = []

        for pid in product_ids:
            cached = await self.cache.get_stock(pid)
            if cached:
                cached_results.append(StockInfo(**cached))
            else:
                uncached_ids.append(pid)

        if uncached_ids:
            fresh = await self.adapter.get_stock(uncached_ids, warehouse_id)
            for stock in fresh:
                await self.cache.set_stock(
                    stock.product_id, stock.model_dump()
                )
            cached_results.extend(fresh)

        return cached_results

    async def get_prices(
        self, product_ids: list[int], pricelist_id: Optional[int] = None
    ) -> list[PriceInfo]:
        uncached_ids = []
        cached_results = []

        for pid in product_ids:
            cached = await self.cache.get_prices(pid)
            if cached:
                cached_results.append(PriceInfo(**cached))
            else:
                uncached_ids.append(pid)

        if uncached_ids:
            fresh = await self.adapter.get_prices(uncached_ids, pricelist_id)
            for price in fresh:
                await self.cache.set_prices(
                    price.product_id, price.model_dump()
                )
            cached_results.extend(fresh)

        return cached_results

    async def get_order_status(self, order_ref: str) -> Optional[OrderStatusInfo]:
        # Orders are not cached (always fresh)
        return await self.adapter.get_order_status(order_ref)

    async def create_quotation(
        self,
        partner_id: int,
        lines: list[dict],
        notes: Optional[str] = None,
    ) -> QuotationResponse:
        return await self.adapter.create_quotation(partner_id, lines, notes)

    # --- Customer methods ---

    async def search_partner_by_email(self, email: str) -> Optional[PartnerInfo]:
        cache_key = f"partner:email:{email.lower().strip()}"
        cached = await self.cache.get(cache_key)
        if cached:
            return PartnerInfo(**cached)
        partner = await self.adapter.search_partner_by_email(email)
        if partner:
            await self.cache.set(cache_key, partner.model_dump(), ttl=1800)
        return partner

    async def get_partner(self, partner_id: int) -> Optional[PartnerInfo]:
        cache_key = f"partner:{partner_id}"
        cached = await self.cache.get(cache_key)
        if cached:
            return PartnerInfo(**cached)
        partner = await self.adapter.get_partner(partner_id)
        if partner:
            await self.cache.set(cache_key, partner.model_dump(), ttl=1800)
        return partner

    async def update_partner(self, partner_id: int, vals: dict) -> bool:
        result = await self.adapter.update_partner(partner_id, vals)
        if result:
            await self.cache.delete(f"partner:{partner_id}")
        return result

    # --- Orders (no cache - real-time) ---

    async def get_partner_orders(
        self, partner_id: int, limit: int = 20, states: Optional[list[str]] = None
    ) -> list[OrderSummary]:
        return await self.adapter.get_partner_orders(partner_id, limit, states)

    async def get_order_details(self, order_id: int, partner_id: int) -> Optional[OrderDetail]:
        return await self.adapter.get_order_details(order_id, partner_id)

    # --- Invoices (5 min cache) ---

    async def get_partner_invoices(self, partner_id: int, limit: int = 20) -> list[InvoiceSummary]:
        cache_key = f"invoices:{partner_id}"
        cached = await self.cache.get(cache_key)
        if cached:
            return [InvoiceSummary(**i) for i in cached]
        invoices = await self.adapter.get_partner_invoices(partner_id, limit)
        await self.cache.set(cache_key, [i.model_dump() for i in invoices], ttl=300)
        return invoices

    async def get_invoice_details(self, invoice_id: int, partner_id: int) -> Optional[InvoiceDetail]:
        return await self.adapter.get_invoice_details(invoice_id, partner_id)

    async def get_invoice_pdf(self, invoice_id: int, partner_id: int) -> Optional[bytes]:
        return await self.adapter.get_invoice_pdf(invoice_id, partner_id)

    async def get_partner_payments(self, partner_id: int, limit: int = 20) -> list[PaymentInfo]:
        return await self.adapter.get_partner_payments(partner_id, limit)

    # --- Deliveries (no cache - real-time) ---

    async def get_partner_deliveries(self, partner_id: int, limit: int = 20) -> list[DeliverySummary]:
        return await self.adapter.get_partner_deliveries(partner_id, limit)

    async def get_delivery_details(self, picking_id: int, partner_id: int) -> Optional[DeliveryDetail]:
        return await self.adapter.get_delivery_details(picking_id, partner_id)

    # --- Support tickets (no cache) ---

    async def create_ticket(
        self, partner_id: int, subject: str, description: str, priority: str = "1"
    ) -> Optional[int]:
        return await self.adapter.create_ticket(partner_id, subject, description, priority)

    async def get_partner_tickets(self, partner_id: int, limit: int = 20) -> list[TicketSummary]:
        return await self.adapter.get_partner_tickets(partner_id, limit)

    async def add_ticket_message(self, ticket_id: int, partner_id: int, body: str) -> bool:
        return await self.adapter.add_ticket_message(ticket_id, partner_id, body)

    # --- Email ---

    async def send_email(
        self, to: str, subject: str, body_html: str, email_from: str = ""
    ) -> int:
        return await self.adapter.send_email(to, subject, body_html, email_from)

    # --- Order cancellation ---

    async def request_order_cancellation(
        self, order_id: int, partner_id: int, reason: str
    ) -> bool:
        return await self.adapter.request_order_cancellation(order_id, partner_id, reason)

    # --- Spending report ---

    async def get_spending_report(self, partner_id: int) -> SpendingReport:
        orders = await self.adapter.get_partner_orders(partner_id, limit=500)
        invoices = await self.adapter.get_partner_invoices(partner_id, limit=500)

        states: dict[str, int] = {}
        total_spent = 0.0
        for o in orders:
            states[o.state] = states.get(o.state, 0) + 1
            if o.state in ("sale", "done"):
                total_spent += o.amount_total

        total_invoiced = sum(i.amount_total for i in invoices if i.state == "posted")
        total_paid = sum(i.amount_total - i.amount_residual for i in invoices if i.state == "posted")
        total_outstanding = sum(i.amount_residual for i in invoices if i.state == "posted")

        return SpendingReport(
            total_orders=len(orders),
            total_spent=total_spent,
            total_invoiced=total_invoiced,
            total_paid=total_paid,
            total_outstanding=total_outstanding,
            orders_by_state=states,
        )
