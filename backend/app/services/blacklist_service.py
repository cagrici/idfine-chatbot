import json
import time

import redis.asyncio as redis


class BlacklistService:
    """Redis-based blacklist for IPs and visitor IDs."""

    BLACKLIST_KEY = "blacklist:entries"

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def is_blacklisted(self, ip: str = "", visitor_id: str = "") -> bool:
        """Check if an IP or visitor_id is blacklisted."""
        if ip and await self.redis.sismember(self.BLACKLIST_KEY, f"ip:{ip}"):
            return True
        if visitor_id and await self.redis.sismember(self.BLACKLIST_KEY, f"visitor:{visitor_id}"):
            return True
        return False

    async def add(self, entry_type: str, value: str, reason: str = "", added_by: str = "") -> bool:
        """Add an IP or visitor to the blacklist.

        entry_type: 'ip' or 'visitor'
        """
        key = f"{entry_type}:{value}"
        await self.redis.sadd(self.BLACKLIST_KEY, key)
        # Store metadata
        await self.redis.hset(f"blacklist:meta:{key}", mapping={
            "type": entry_type,
            "value": value,
            "reason": reason,
            "added_by": added_by,
            "added_at": str(time.time()),
        })
        return True

    async def remove(self, entry_type: str, value: str) -> bool:
        """Remove an entry from the blacklist."""
        key = f"{entry_type}:{value}"
        await self.redis.srem(self.BLACKLIST_KEY, key)
        await self.redis.delete(f"blacklist:meta:{key}")
        return True

    async def list_all(self) -> list[dict]:
        """List all blacklisted entries with metadata."""
        entries = await self.redis.smembers(self.BLACKLIST_KEY)
        result = []
        for entry in entries:
            if isinstance(entry, bytes):
                entry = entry.decode()
            meta = await self.redis.hgetall(f"blacklist:meta:{entry}")
            if meta:
                decoded = {}
                for k, v in meta.items():
                    k = k.decode() if isinstance(k, bytes) else k
                    v = v.decode() if isinstance(v, bytes) else v
                    decoded[k] = v
                result.append(decoded)
            else:
                parts = entry.split(":", 1)
                result.append({"type": parts[0], "value": parts[1] if len(parts) > 1 else entry})
        return result
