from abc import ABC, abstractmethod
from typing import Any, Optional

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
    StockInfo,
    TicketSummary,
)


class OdooAdapter(ABC):
    """Abstract base class for Odoo API adapters."""

    def __init__(
        self,
        url: str,
        db: str,
        username: str = "",
        password: str = "",
        api_key: str = "",
        verify_ssl: bool = True,
    ):
        self.url = url.rstrip("/")
        self.db = db
        self.username = username
        self.password = password
        self.api_key = api_key
        self.verify_ssl = verify_ssl

    @abstractmethod
    async def authenticate(self) -> int:
        """Authenticate and return uid."""
        ...

    @abstractmethod
    async def search_products(
        self, query: str, limit: int = 20
    ) -> list[ProductInfo]:
        """Search products by name or code."""
        ...

    @abstractmethod
    async def get_stock(
        self, product_ids: list[int], warehouse_id: Optional[int] = None
    ) -> list[StockInfo]:
        """Get stock levels for given products."""
        ...

    @abstractmethod
    async def get_prices(
        self, product_ids: list[int], pricelist_id: Optional[int] = None
    ) -> list[PriceInfo]:
        """Get prices for given products."""
        ...

    @abstractmethod
    async def get_order_status(self, order_ref: str) -> Optional[OrderStatusInfo]:
        """Get order status by reference number."""
        ...

    @abstractmethod
    async def create_quotation(
        self,
        partner_id: int,
        lines: list[dict],
        notes: Optional[str] = None,
    ) -> QuotationResponse:
        """Create a sales quotation in Odoo."""
        ...

    @abstractmethod
    async def call(
        self, model: str, method: str, args: list, kwargs: Optional[dict] = None
    ) -> Any:
        """Generic RPC call to Odoo."""
        ...

    # --- Customer methods (concrete - implemented by JsonRpcAdapter, default raises) ---

    async def search_partner_by_email(self, email: str) -> Optional[PartnerInfo]:
        raise NotImplementedError

    async def get_partner(self, partner_id: int) -> Optional[PartnerInfo]:
        raise NotImplementedError

    async def update_partner(self, partner_id: int, vals: dict) -> bool:
        raise NotImplementedError

    async def get_partner_orders(
        self, partner_id: int, limit: int = 20, states: Optional[list[str]] = None
    ) -> list[OrderSummary]:
        raise NotImplementedError

    async def get_order_details(self, order_id: int, partner_id: int) -> Optional[OrderDetail]:
        raise NotImplementedError

    async def get_partner_invoices(self, partner_id: int, limit: int = 20) -> list[InvoiceSummary]:
        raise NotImplementedError

    async def get_invoice_details(self, invoice_id: int, partner_id: int) -> Optional[InvoiceDetail]:
        raise NotImplementedError

    async def get_invoice_pdf(self, invoice_id: int, partner_id: int) -> Optional[bytes]:
        raise NotImplementedError

    async def get_partner_payments(self, partner_id: int, limit: int = 20) -> list[PaymentInfo]:
        raise NotImplementedError

    async def get_partner_deliveries(self, partner_id: int, limit: int = 20) -> list[DeliverySummary]:
        raise NotImplementedError

    async def get_delivery_details(self, picking_id: int, partner_id: int) -> Optional[DeliveryDetail]:
        raise NotImplementedError

    async def create_ticket(
        self, partner_id: int, subject: str, description: str, priority: str = "1"
    ) -> Optional[int]:
        raise NotImplementedError

    async def get_partner_tickets(self, partner_id: int, limit: int = 20) -> list[TicketSummary]:
        raise NotImplementedError

    async def add_ticket_message(self, ticket_id: int, partner_id: int, body: str) -> bool:
        raise NotImplementedError

    async def send_email(self, to: str, subject: str, body_html: str, email_from: str = "") -> int:
        raise NotImplementedError

    async def request_order_cancellation(
        self, order_id: int, partner_id: int, reason: str
    ) -> bool:
        raise NotImplementedError
