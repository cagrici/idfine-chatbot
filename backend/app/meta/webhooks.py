import asyncio
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Query, Request, Response

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/webhooks/meta", tags=["meta-webhooks"])


@router.get("")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Meta webhook verification (GET).

    Meta sends a GET request with hub.mode, hub.verify_token, hub.challenge.
    We must return hub.challenge as plain text if verify_token matches.
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.meta_verify_token:
        logger.info("Meta webhook verified successfully")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Meta webhook verification failed: mode=%s", hub_mode)
    return Response(content="Verification failed", status_code=403)


@router.post("")
async def receive_webhook(request: Request):
    """Meta webhook event handler (POST).

    Receives events from Facebook Messenger, Instagram DM, and WhatsApp.
    Verifies X-Hub-Signature-256 then dispatches to handler.
    """
    body = await request.body()

    signature = request.headers.get("X-Hub-Signature-256", "")
    if settings.meta_app_secret and not _verify_signature(body, signature):
        logger.warning("Meta webhook signature verification failed")
        return Response(content="Invalid signature", status_code=403)

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Meta webhook: invalid JSON body")
        return {"status": "error"}

    logger.info("Meta webhook received: object=%s", payload.get("object"))

    from app.meta.handler import handle_meta_event
    asyncio.create_task(handle_meta_event(payload))

    return {"status": "ok"}


def _verify_signature(body: bytes, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 from Meta."""
    if not signature_header.startswith("sha256="):
        return False
    expected = signature_header[7:]
    computed = hmac.new(
        settings.meta_app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, expected)
