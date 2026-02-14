import json
import logging
from typing import Any, Optional

import redis.asyncio as redis

logger = logging.getLogger(__name__)


class CacheService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def get(self, key: str) -> Optional[Any]:
        data = await self.redis.get(f"cache:{key}")
        if data:
            return json.loads(data)
        return None

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        await self.redis.set(
            f"cache:{key}", json.dumps(value, default=str), ex=ttl
        )

    async def delete(self, key: str) -> None:
        await self.redis.delete(f"cache:{key}")

    async def delete_pattern(self, pattern: str) -> None:
        keys = []
        async for key in self.redis.scan_iter(f"cache:{pattern}"):
            keys.append(key)
        if keys:
            await self.redis.delete(*keys)

    # Odoo-specific cache methods

    async def get_products(self, query: str) -> Optional[list[dict]]:
        return await self.get(f"odoo:products:{query}")

    async def set_products(self, query: str, data: list[dict]) -> None:
        await self.set(f"odoo:products:{query}", data, ttl=900)  # 15 min

    async def get_stock(self, product_id: int) -> Optional[dict]:
        return await self.get(f"odoo:stock:{product_id}")

    async def set_stock(self, product_id: int, data: dict) -> None:
        await self.set(f"odoo:stock:{product_id}", data, ttl=60)  # 1 min

    async def get_prices(self, product_id: int) -> Optional[dict]:
        return await self.get(f"odoo:price:{product_id}")

    async def set_prices(self, product_id: int, data: dict) -> None:
        await self.set(f"odoo:price:{product_id}", data, ttl=3600)  # 1 hour
