from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ProductInfo(BaseModel):
    id: int
    name: str
    default_code: Optional[str] = None
    description: Optional[str] = None
    list_price: float
    category: Optional[str] = None
    image_url: Optional[str] = None


class StockInfo(BaseModel):
    product_id: int
    product_name: str
    qty_available: float
    warehouse: Optional[str] = None
    last_updated: datetime


class PriceInfo(BaseModel):
    product_id: int
    product_name: str
    list_price: float
    currency: str = "TRY"
    pricelist_name: Optional[str] = None


class OrderStatusInfo(BaseModel):
    order_ref: str
    state: str  # draft, sent, sale, done, cancel
    partner_name: str
    date_order: datetime
    amount_total: float
    currency: str = "TRY"
    invoice_status: Optional[str] = None
    delivery_status: Optional[str] = None


class QuotationRequest(BaseModel):
    partner_id: int
    lines: list["QuotationLine"]
    notes: Optional[str] = None


class QuotationLine(BaseModel):
    product_id: int
    quantity: float
    unit_price: Optional[float] = None  # None = use default pricelist


class QuotationResponse(BaseModel):
    order_id: int
    order_ref: str
    amount_total: float
    status: str
    message: str


# --- Customer / Partner schemas ---

class PartnerInfo(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    mobile: Optional[str] = None
    street: Optional[str] = None
    street2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: Optional[str] = None
    vat: Optional[str] = None
    company_name: Optional[str] = None
    customer_rank: int = 0


class OrderSummary(BaseModel):
    id: int
    name: str
    state: str
    date_order: Optional[str] = None
    amount_total: float = 0
    currency: str = "TRY"
    invoice_status: Optional[str] = None


class OrderDetail(BaseModel):
    id: int
    name: str
    state: str
    date_order: Optional[str] = None
    amount_untaxed: float = 0
    amount_tax: float = 0
    amount_total: float = 0
    currency: str = "TRY"
    invoice_status: Optional[str] = None
    note: Optional[str] = None
    lines: list["OrderLineDetail"] = []


class OrderLineDetail(BaseModel):
    id: int
    product_name: str
    product_code: Optional[str] = None
    quantity: float
    price_unit: float
    price_subtotal: float
    product_uom: Optional[str] = None


class InvoiceSummary(BaseModel):
    id: int
    name: str
    state: str  # draft, posted, cancel
    move_type: str  # out_invoice, out_refund
    date: Optional[str] = None
    invoice_date_due: Optional[str] = None
    amount_total: float = 0
    amount_residual: float = 0
    currency: str = "TRY"
    payment_state: Optional[str] = None


class InvoiceDetail(BaseModel):
    id: int
    name: str
    state: str
    move_type: str
    date: Optional[str] = None
    invoice_date_due: Optional[str] = None
    amount_untaxed: float = 0
    amount_tax: float = 0
    amount_total: float = 0
    amount_residual: float = 0
    currency: str = "TRY"
    payment_state: Optional[str] = None
    lines: list["InvoiceLineDetail"] = []


class InvoiceLineDetail(BaseModel):
    id: int
    product_name: Optional[str] = None
    quantity: float
    price_unit: float
    price_subtotal: float


class PaymentInfo(BaseModel):
    id: int
    name: str
    date: Optional[str] = None
    amount: float
    currency: str = "TRY"
    state: str
    payment_type: str


class DeliverySummary(BaseModel):
    id: int
    name: str
    state: str
    origin: Optional[str] = None
    scheduled_date: Optional[str] = None
    date_done: Optional[str] = None
    carrier: Optional[str] = None
    tracking_ref: Optional[str] = None


class DeliveryDetail(BaseModel):
    id: int
    name: str
    state: str
    origin: Optional[str] = None
    scheduled_date: Optional[str] = None
    date_done: Optional[str] = None
    carrier: Optional[str] = None
    tracking_ref: Optional[str] = None
    lines: list["DeliveryLineDetail"] = []


class DeliveryLineDetail(BaseModel):
    id: int
    product_name: str
    quantity_done: float
    product_uom: Optional[str] = None


class TicketSummary(BaseModel):
    id: int
    name: str
    stage: Optional[str] = None
    priority: Optional[str] = None
    create_date: Optional[str] = None
    description: Optional[str] = None


class SpendingReport(BaseModel):
    total_orders: int = 0
    total_spent: float = 0
    currency: str = "TRY"
    total_invoiced: float = 0
    total_paid: float = 0
    total_outstanding: float = 0
    orders_by_state: dict[str, int] = {}
