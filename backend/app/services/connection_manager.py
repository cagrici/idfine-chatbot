import asyncio
import json
import logging
import time

import redis.asyncio as redis
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections and message routing for live support."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        # In-process connection registries
        self.widget_connections: dict[str, WebSocket] = {}  # conversation_id → customer WS
        self.agent_connections: dict[str, WebSocket] = {}  # conversation_id → agent WS
        self.notification_connections: list[WebSocket] = []  # all agent notification WSes
        self._pubsub_task: asyncio.Task | None = None

    # ── Connection registration ──────────────────────────────────

    async def register_widget(self, conversation_id: str, ws: WebSocket):
        self.widget_connections[conversation_id] = ws
        logger.debug("Widget registered for conversation %s", conversation_id)

    async def unregister_widget(self, conversation_id: str):
        self.widget_connections.pop(conversation_id, None)
        logger.debug("Widget unregistered for conversation %s", conversation_id)

    async def register_agent(self, conversation_id: str, ws: WebSocket):
        self.agent_connections[conversation_id] = ws
        logger.debug("Agent registered for conversation %s", conversation_id)

    async def unregister_agent(self, conversation_id: str):
        self.agent_connections.pop(conversation_id, None)
        logger.debug("Agent unregistered for conversation %s", conversation_id)

    async def register_notification_listener(self, ws: WebSocket):
        self.notification_connections.append(ws)

    async def unregister_notification_listener(self, ws: WebSocket):
        try:
            self.notification_connections.remove(ws)
        except ValueError:
            pass

    # ── Message routing ──────────────────────────────────────────

    async def send_to_widget(self, conversation_id: str, data: dict) -> bool:
        ws = self.widget_connections.get(conversation_id)
        if ws:
            try:
                await ws.send_json(data)
                return True
            except Exception:
                logger.warning("Failed to send to widget %s", conversation_id)
                self.widget_connections.pop(conversation_id, None)
        return False

    async def send_to_agent(self, conversation_id: str, data: dict) -> bool:
        ws = self.agent_connections.get(conversation_id)
        if ws:
            try:
                await ws.send_json(data)
                return True
            except Exception:
                logger.warning("Failed to send to agent %s", conversation_id)
                self.agent_connections.pop(conversation_id, None)
        return False

    def has_widget_connection(self, conversation_id: str) -> bool:
        return conversation_id in self.widget_connections

    def has_agent_connection(self, conversation_id: str) -> bool:
        return conversation_id in self.agent_connections

    # ── Agent notifications (Redis pub/sub) ──────────────────────

    async def notify_agents(self, event: dict):
        """Broadcast event to all connected agent notification listeners."""
        message = json.dumps(event, default=str)
        # Publish to Redis channel for multi-process support
        await self.redis.publish("live_support:agents", message)
        # Also send to local listeners directly
        disconnected = []
        for ws in self.notification_connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            try:
                self.notification_connections.remove(ws)
            except ValueError:
                pass

    async def notify_new_escalation(self, conversation_id: str, preview: str, source_group_id: str | None = None):
        await self.notify_agents({
            "type": "new_escalation",
            "conversation_id": conversation_id,
            "preview": preview[:200],
            "source_group_id": source_group_id,
            "timestamp": time.time(),
        })

    async def notify_conversation_update(self, conversation_id: str, event: str):
        waiting_count = await self.redis.zcard("live_support:queue")
        await self.notify_agents({
            "type": "queue_update",
            "event": event,
            "conversation_id": conversation_id,
            "waiting_count": waiting_count,
        })

    # ── Queue management ─────────────────────────────────────────

    async def add_to_queue(self, conversation_id: str, data: dict):
        """Add conversation to the waiting queue."""
        await self.redis.zadd("live_support:queue", {conversation_id: time.time()})
        await self.redis.hset(
            f"live_support:queue:{conversation_id}",
            mapping={
                "conversation_id": conversation_id,
                "visitor_id": data.get("visitor_id", ""),
                "last_message": data.get("last_message", "")[:200],
                "source_group_id": data.get("source_group_id", ""),
                "channel": data.get("channel", "widget"),
                "queued_at": str(time.time()),
            },
        )
        await self.redis.expire(f"live_support:queue:{conversation_id}", 86400)  # 24h TTL

    async def remove_from_queue(self, conversation_id: str):
        """Remove conversation from the waiting queue."""
        await self.redis.zrem("live_support:queue", conversation_id)
        await self.redis.delete(f"live_support:queue:{conversation_id}")

    async def get_waiting_conversations(self) -> list[dict]:
        """Get all conversations in the waiting queue."""
        conv_ids = await self.redis.zrange("live_support:queue", 0, -1)
        result = []
        for conv_id in conv_ids:
            data = await self.redis.hgetall(f"live_support:queue:{conv_id}")
            if data:
                result.append(data)
            else:
                # Stale entry, clean up
                await self.redis.zrem("live_support:queue", conv_id)
        return result

    async def get_queue_count(self) -> int:
        return await self.redis.zcard("live_support:queue")

    # ── Auto-assignment ──────────────────────────────────────────

    async def try_auto_assign(self, conversation_id: str, db) -> dict | None:
        """Try to auto-assign a conversation to an available online agent.

        Returns assignment info dict if assigned, None if no agent available.
        Uses round-robin: assigns to the online agent with fewest active conversations.
        """
        from sqlalchemy import select, func as sa_func
        from app.models.user import User
        from app.models.conversation import Conversation
        import uuid as uuid_mod

        # Find online agents
        agents_result = await db.execute(
            select(User.id, User.full_name)
            .where(User.is_active == True)
            .where(User.agent_status == "online")
            .where(User.role.in_(["admin", "agent", "manager"]))
        )
        agents = agents_result.all()
        if not agents:
            return None

        # Count active conversations per agent
        counts = {}
        for agent in agents:
            count_result = await db.execute(
                select(sa_func.count())
                .select_from(Conversation)
                .where(Conversation.assigned_agent_id == agent.id)
                .where(Conversation.mode == "human")
                .where(Conversation.status == "assigned")
            )
            counts[agent.id] = count_result.scalar() or 0

        # Pick agent with fewest active conversations
        best_agent = min(agents, key=lambda a: counts[a.id])

        # Assign the conversation
        conv_uuid = uuid_mod.UUID(conversation_id) if isinstance(conversation_id, str) else conversation_id
        conv_result = await db.execute(
            select(Conversation).where(Conversation.id == conv_uuid)
        )
        conv = conv_result.scalar_one_or_none()
        if not conv:
            return None

        conv.status = "assigned"
        conv.mode = "human"
        conv.assigned_agent_id = best_agent.id
        await db.commit()

        # Remove from queue
        await self.remove_from_queue(str(conversation_id))

        return {
            "agent_id": str(best_agent.id),
            "agent_name": best_agent.full_name,
        }
