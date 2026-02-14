import logging

from fastapi import APIRouter, Request

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/webhooks/odoo", tags=["odoo-webhooks"])


@router.post("/product-update")
async def product_update(request: Request):
    """Handle Odoo product update webhook.

    Configure in Odoo Studio > Automated Actions > Webhooks:
    - Model: product.product
    - Trigger: On Creation and Update
    - URL: https://your-domain/api/webhooks/odoo/product-update
    """
    body = await request.json()
    logger.info("Odoo product update webhook received: %s", body)

    # TODO: Invalidate product cache in Redis
    # TODO: Re-sync updated product data

    return {"status": "received"}


@router.post("/stock-update")
async def stock_update(request: Request):
    """Handle Odoo stock update webhook."""
    body = await request.json()
    logger.info("Odoo stock update webhook received: %s", body)

    # TODO: Invalidate stock cache in Redis

    return {"status": "received"}


@router.post("/order-update")
async def order_update(request: Request):
    """Handle Odoo order status update webhook."""
    body = await request.json()
    logger.info("Odoo order update webhook received: %s", body)

    # TODO: Notify relevant WebSocket connections about order changes

    return {"status": "received"}
