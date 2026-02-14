import logging
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db.database import async_session
from app.dependencies import get_connection_manager
from app.models.conversation import Conversation, Message
from app.services.meta_sender import get_meta_sender

logger = logging.getLogger(__name__)
settings = get_settings()


async def handle_meta_event(payload: dict):
    """Dispatch a Meta webhook payload to the appropriate handler."""
    obj = payload.get("object")

    for entry in payload.get("entry", []):
        if obj == "page":
            for event in entry.get("messaging", []):
                await _handle_message(event, channel="messenger")

        elif obj == "instagram":
            for event in entry.get("messaging", []):
                await _handle_message(event, channel="instagram")

        elif obj == "whatsapp_business_account":
            for change in entry.get("changes", []):
                if change.get("field") == "messages":
                    value = change.get("value", {})
                    for message in value.get("messages", []):
                        contact = _find_contact(value, message.get("from"))
                        await _handle_whatsapp_message(message, contact, value)


def _find_contact(value: dict, from_number: str) -> dict:
    """Find contact info from WhatsApp webhook value."""
    for contact in value.get("contacts", []):
        if contact.get("wa_id") == from_number:
            return contact
    return {}


async def _handle_message(event: dict, channel: str):
    """Handle a Facebook Messenger or Instagram DM message event."""
    message = event.get("message")
    if not message:
        return

    if message.get("is_echo"):
        return

    sender_id = event["sender"]["id"]
    text = message.get("text", "")

    if not text:
        logger.info("Skipping non-text %s message from %s", channel, sender_id)
        return

    await _process_social_message(
        channel=channel,
        platform_sender_id=sender_id,
        text=text,
        platform_metadata={
            "sender_id": sender_id,
            "message_id": message.get("mid", ""),
            "page_id": event.get("recipient", {}).get("id", ""),
        },
    )


async def _handle_whatsapp_message(message: dict, contact: dict, value: dict):
    """Handle a WhatsApp Business message."""
    if message.get("type") != "text":
        logger.info("Skipping non-text WhatsApp message type=%s", message.get("type"))
        return

    sender_phone = message.get("from", "")
    text = message.get("text", {}).get("body", "")
    contact_name = contact.get("profile", {}).get("name", "")

    if not text:
        return

    phone_number_id = value.get("metadata", {}).get("phone_number_id", "")

    await _process_social_message(
        channel="whatsapp",
        platform_sender_id=sender_phone,
        text=text,
        platform_metadata={
            "sender_phone": sender_phone,
            "contact_name": contact_name,
            "message_id": message.get("id", ""),
            "phone_number_id": phone_number_id,
            "wa_id": contact.get("wa_id", sender_phone),
        },
    )


async def _process_social_message(
    channel: str,
    platform_sender_id: str,
    text: str,
    platform_metadata: dict,
):
    """Core processing: find/create conversation, run ChatService, send reply."""
    visitor_id = f"{channel}:{platform_sender_id}"

    try:
        async with async_session() as db:
            # Find existing active conversation for this social user
            result = await db.execute(
                select(Conversation)
                .where(Conversation.visitor_id == visitor_id)
                .where(Conversation.channel == channel)
                .where(Conversation.status.in_(["active", "assigned", "waiting"]))
                .order_by(Conversation.updated_at.desc())
                .limit(1)
            )
            conv = result.scalar_one_or_none()
            conversation_id = str(conv.id) if conv else None

            # Store platform metadata on conversation
            if conv and not conv.metadata_.get("platform_sender_id"):
                conv.metadata_ = {
                    **conv.metadata_,
                    "platform_sender_id": platform_sender_id,
                    **platform_metadata,
                }

            # Check if conversation is in human mode (agent takeover)
            if conv and conv.mode == "human":
                user_msg = Message(
                    conversation_id=conv.id,
                    role="user",
                    content=text,
                    sender_type="user",
                )
                db.add(user_msg)
                await db.commit()

                cm = await get_connection_manager()
                await cm.send_to_agent(str(conv.id), {
                    "type": "customer_message",
                    "content": text,
                    "conversation_id": str(conv.id),
                    "message_id": str(user_msg.id),
                    "channel": channel,
                })
                return

            # AI mode: process via ChatService
            from app.api.websocket import _create_chat_dependencies
            deps = await _create_chat_dependencies(settings)

            from app.services.chat_service import ChatService
            chat = ChatService(
                db=db,
                rag_engine=deps["rag_engine"],
                llm_service=deps["llm_service"],
                odoo_service=deps["odoo_service"],
                intent_classifier=deps["classifier"],
                flow_manager=deps["flow_manager"],
                customer_session=deps["customer_session"],
                otp_service=deps["otp_service"],
            )

            source_group_id = settings.meta_source_group_id or None

            ai_result = await chat.process_message(
                user_message=text,
                conversation_id=conversation_id,
                visitor_id=visitor_id,
                channel=channel,
                source_group_id=source_group_id,
            )

            # Update conversation metadata with platform info
            new_conv_id = ai_result.get("conversation_id")
            if new_conv_id:
                conv_result = await db.execute(
                    select(Conversation).where(
                        Conversation.id == uuid.UUID(new_conv_id)
                    )
                )
                new_conv = conv_result.scalar_one_or_none()
                if new_conv:
                    new_conv.metadata_ = {
                        **new_conv.metadata_,
                        "platform_sender_id": platform_sender_id,
                        **platform_metadata,
                    }

            await db.commit()

            # Send AI response back to the social platform
            response_text = ai_result.get("content", "")
            if response_text:
                sender = get_meta_sender()
                await sender.send_message(channel, platform_sender_id, response_text)

    except Exception as e:
        logger.error("Error processing %s message from %s: %s", channel, platform_sender_id, e)
