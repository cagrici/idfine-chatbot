"""Customer session service - manages authenticated customer sessions in Redis."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import redis.asyncio as redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class CustomerSession:
    partner_id: int
    email: str
    name: str
    verified_at: str
    visitor_id: str
    pricelist_id: int | None = None
    pricelist_name: str | None = None
    discount_percent: float = 0


class CustomerSessionService:
    """Manages customer sessions in Redis after OTP verification."""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    def _session_key(self, visitor_id: str) -> str:
        return f"customer_session:{visitor_id}"

    async def create_session(
        self,
        visitor_id: str,
        partner_id: int,
        email: str,
        name: str,
        pricelist_id: int | None = None,
        pricelist_name: str | None = None,
        discount_percent: float = 0,
    ) -> CustomerSession:
        """Create a new customer session after OTP verification."""
        now = datetime.now(timezone.utc).isoformat()
        session_data = {
            "partner_id": partner_id,
            "email": email,
            "name": name,
            "verified_at": now,
            "visitor_id": visitor_id,
            "pricelist_id": pricelist_id,
            "pricelist_name": pricelist_name,
            "discount_percent": discount_percent,
        }

        key = self._session_key(visitor_id)
        await self.redis.set(
            key,
            json.dumps(session_data),
            ex=settings.customer_session_ttl_seconds,
        )

        logger.info(
            "Customer session created: visitor=%s, partner=%d, name=%s",
            visitor_id, partner_id, name,
        )

        return CustomerSession(**session_data)

    async def get_session(self, visitor_id: str) -> CustomerSession | None:
        """Get an active customer session."""
        key = self._session_key(visitor_id)
        data = await self.redis.get(key)
        if not data:
            return None

        session_data = json.loads(data)
        return CustomerSession(**session_data)

    async def extend_session(self, visitor_id: str) -> bool:
        """Extend session TTL on activity."""
        key = self._session_key(visitor_id)
        exists = await self.redis.exists(key)
        if exists:
            await self.redis.expire(key, settings.customer_session_ttl_seconds)
            return True
        return False

    async def destroy_session(self, visitor_id: str) -> bool:
        """Destroy a customer session (logout)."""
        key = self._session_key(visitor_id)
        deleted = await self.redis.delete(key)
        if deleted:
            logger.info("Customer session destroyed: visitor=%s", visitor_id)
        return bool(deleted)

    async def is_authenticated(self, visitor_id: str) -> bool:
        """Quick check if a visitor has an active customer session."""
        key = self._session_key(visitor_id)
        return bool(await self.redis.exists(key))
