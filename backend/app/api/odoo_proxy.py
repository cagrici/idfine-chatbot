from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.core.exceptions import NotFoundError, OdooConnectionError
from app.dependencies import get_current_user, get_redis
from app.models.user import User
from app.schemas.odoo import (
    OrderStatusInfo,
    PriceInfo,
    ProductInfo,
    QuotationRequest,
    QuotationResponse,
    StockInfo,
)
from app.services.cache_service import CacheService
from app.services.odoo_service import OdooService, create_odoo_adapter

router = APIRouter(prefix="/odoo", tags=["odoo"])


async def get_odoo_service(
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> OdooService:
    if not settings.odoo_url:
        raise OdooConnectionError("Odoo bağlantısı yapılandırılmamış")
    adapter = create_odoo_adapter()
    cache = CacheService(redis_client)
    return OdooService(adapter, cache)


@router.get("/products", response_model=list[ProductInfo])
async def search_products(
    query: str,
    user: Annotated[User, Depends(get_current_user)],
    odoo: Annotated[OdooService, Depends(get_odoo_service)],
    limit: int = 20,
):
    return await odoo.search_products(query, limit)


@router.get("/stock/{product_id}", response_model=list[StockInfo])
async def get_stock(
    product_id: int,
    user: Annotated[User, Depends(get_current_user)],
    odoo: Annotated[OdooService, Depends(get_odoo_service)],
):
    return await odoo.get_stock([product_id])


@router.get("/prices/{product_id}", response_model=list[PriceInfo])
async def get_prices(
    product_id: int,
    user: Annotated[User, Depends(get_current_user)],
    odoo: Annotated[OdooService, Depends(get_odoo_service)],
):
    return await odoo.get_prices([product_id])


@router.get("/orders/{order_ref}", response_model=OrderStatusInfo)
async def get_order_status(
    order_ref: str,
    user: Annotated[User, Depends(get_current_user)],
    odoo: Annotated[OdooService, Depends(get_odoo_service)],
):
    order = await odoo.get_order_status(order_ref)
    if not order:
        raise NotFoundError(f"Sipariş bulunamadı: {order_ref}")
    return order


@router.post("/quotation", response_model=QuotationResponse)
async def create_quotation(
    body: QuotationRequest,
    user: Annotated[User, Depends(get_current_user)],
    odoo: Annotated[OdooService, Depends(get_odoo_service)],
):
    lines = [
        {"product_id": l.product_id, "quantity": l.quantity, "unit_price": l.unit_price}
        for l in body.lines
    ]
    return await odoo.create_quotation(body.partner_id, lines, body.notes)
