"""Customer-facing API endpoints (invoice PDF download, etc.)."""

import hashlib
import logging
import secrets
from typing import Annotated

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from app.config import Settings, get_settings
from app.dependencies import get_redis, get_visitor_id
from app.services.cache_service import CacheService
from app.services.customer_session_service import CustomerSessionService
from app.services.odoo_service import OdooService, create_odoo_adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/customer", tags=["customer"])

# Short-lived download token TTL (5 minutes)
DOWNLOAD_TOKEN_TTL = 300


async def _get_customer_services(
    redis_client: redis.Redis, settings: Settings
) -> tuple[CustomerSessionService, OdooService | None]:
    """Create customer services."""
    session_service = CustomerSessionService(redis_client)
    cache = CacheService(redis_client)

    odoo_service = None
    if settings.odoo_url:
        adapter = create_odoo_adapter()
        odoo_service = OdooService(adapter, cache)

    return session_service, odoo_service


@router.post("/invoice/token")
async def create_invoice_download_token(
    invoice_id: int,
    visitor_id: Annotated[str, Depends(get_visitor_id)],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Generate a short-lived token for invoice PDF download.

    The widget requests this endpoint to get a download token,
    then uses the token in a GET request to download the PDF.
    This avoids exposing partner_id or invoice_id directly.
    """
    session_service, odoo_service = await _get_customer_services(redis_client, settings)

    # Verify customer session
    session = await session_service.get_session(visitor_id)
    if not session:
        raise HTTPException(status_code=401, detail="Oturum bulunamadi. Lutfen giris yapin.")

    if not odoo_service:
        raise HTTPException(status_code=503, detail="Odoo baglantisi yapilamiyor.")

    # Verify invoice belongs to this customer
    invoices = await odoo_service.get_partner_invoices(session.partner_id, limit=100)
    if not any(inv.id == invoice_id for inv in invoices):
        raise HTTPException(status_code=403, detail="Bu faturaya erisim izniniz yok.")

    # Generate download token
    token = secrets.token_urlsafe(32)
    token_key = f"invoice_download:{token}"
    token_data = f"{invoice_id}:{session.partner_id}"

    await redis_client.set(token_key, token_data, ex=DOWNLOAD_TOKEN_TTL)

    return {"download_token": token, "expires_in": DOWNLOAD_TOKEN_TTL}


@router.get("/invoice/download")
async def download_invoice_pdf(
    token: Annotated[str, Query(description="Download token from /invoice/token")],
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Download invoice PDF using a short-lived token.

    This endpoint is called directly (e.g., window.open) so it uses
    GET with a query parameter instead of headers.
    """
    # Validate token
    token_key = f"invoice_download:{token}"
    token_data = await redis_client.get(token_key)

    if not token_data:
        raise HTTPException(status_code=401, detail="Gecersiz veya suresi dolmus indirme tokeni.")

    # Delete token after use (one-time use)
    await redis_client.delete(token_key)

    # Parse token data
    try:
        parts = token_data.split(":")
        invoice_id = int(parts[0])
        partner_id = int(parts[1])
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail="Gecersiz token verisi.")

    # Create Odoo service and fetch PDF
    cache = CacheService(redis_client)
    if not settings.odoo_url:
        raise HTTPException(status_code=503, detail="Odoo baglantisi yapilamiyor.")

    adapter = create_odoo_adapter()
    odoo_service = OdooService(adapter, cache)

    pdf_data = await odoo_service.get_invoice_pdf(invoice_id, partner_id)
    if not pdf_data:
        raise HTTPException(status_code=404, detail="Fatura PDF'i bulunamadi.")

    # Get invoice name for filename
    invoice_detail = await odoo_service.get_invoice_details(invoice_id, partner_id)
    filename = f"fatura_{invoice_detail.name if invoice_detail else invoice_id}.pdf"
    # Sanitize filename
    filename = filename.replace("/", "_").replace("\\", "_")

    return Response(
        content=pdf_data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
