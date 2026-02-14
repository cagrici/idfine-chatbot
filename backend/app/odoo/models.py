# Re-export Odoo schemas for convenience
from app.schemas.odoo import (
    OrderStatusInfo,
    PriceInfo,
    ProductInfo,
    QuotationLine,
    QuotationRequest,
    QuotationResponse,
    StockInfo,
)

__all__ = [
    "ProductInfo",
    "StockInfo",
    "PriceInfo",
    "OrderStatusInfo",
    "QuotationRequest",
    "QuotationLine",
    "QuotationResponse",
]
