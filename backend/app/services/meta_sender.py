import logging
from typing import Literal

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

ChannelType = Literal["messenger", "instagram", "whatsapp"]

SOCIAL_CHANNELS = ("messenger", "instagram", "whatsapp")


class MetaSender:
    """Sends messages to Meta platforms (Messenger, Instagram, WhatsApp)."""

    def __init__(self):
        settings = get_settings()
        self.graph_base = f"https://graph.facebook.com/{settings.meta_graph_api_version}"

    async def send_message(
        self,
        channel: ChannelType,
        recipient_id: str,
        text: str,
    ) -> bool:
        """Send a text message to the appropriate Meta platform."""
        if channel == "whatsapp":
            return await self._send_whatsapp(recipient_id, text)
        else:
            return await self._send_messenger_or_instagram(recipient_id, text)

    async def _send_messenger_or_instagram(self, recipient_id: str, text: str) -> bool:
        """Send via Facebook Messenger or Instagram DM (same API)."""
        settings = get_settings()
        url = f"{self.graph_base}/me/messages"
        payload = {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
            "messaging_type": "RESPONSE",
        }
        headers = {
            "Authorization": f"Bearer {settings.meta_page_access_token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code == 200:
                    logger.info("Meta message sent to %s", recipient_id)
                    return True
                else:
                    logger.error("Meta send failed (%d): %s", resp.status_code, resp.text)
                    return False
        except Exception as e:
            logger.error("Meta send error: %s", e)
            return False

    async def _send_whatsapp(self, recipient_phone: str, text: str) -> bool:
        """Send via WhatsApp Business Cloud API."""
        settings = get_settings()
        phone_number_id = settings.meta_whatsapp_phone_number_id
        url = f"{self.graph_base}/{phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient_phone,
            "type": "text",
            "text": {"body": text},
        }
        token = settings.meta_whatsapp_access_token or settings.meta_page_access_token
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    logger.info("WhatsApp message sent to %s", recipient_phone)
                    return True
                else:
                    logger.error("WhatsApp send failed (%d): %s", resp.status_code, resp.text)
                    return False
        except Exception as e:
            logger.error("WhatsApp send error: %s", e)
            return False


_sender: MetaSender | None = None


def get_meta_sender() -> MetaSender:
    global _sender
    if _sender is None:
        _sender = MetaSender()
    return _sender


def get_social_recipient(conversation) -> str:
    """Extract the platform recipient ID from conversation metadata."""
    metadata = conversation.metadata_ or {}
    if conversation.channel == "whatsapp":
        return metadata.get("sender_phone") or metadata.get("wa_id", "")
    return metadata.get("sender_id", "")
