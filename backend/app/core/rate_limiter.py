import time
from typing import Optional

import redis.asyncio as redis

from app.config import get_settings

settings = get_settings()


class RateLimiter:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def is_rate_limited(
        self,
        key: str,
        max_requests: int,
        window_seconds: int = 60,
    ) -> tuple[bool, Optional[int]]:
        """Check if a key has exceeded the rate limit.

        Returns (is_limited, retry_after_seconds).
        """
        now = time.time()
        window_start = now - window_seconds
        pipe_key = f"ratelimit:{key}"

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(pipe_key, 0, window_start)
        pipe.zadd(pipe_key, {str(now): now})
        pipe.zcard(pipe_key)
        pipe.expire(pipe_key, window_seconds)
        results = await pipe.execute()

        request_count = results[2]

        if request_count > max_requests:
            oldest = await self.redis.zrange(pipe_key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(window_seconds - (now - oldest[0][1])) + 1
                return True, max(retry_after, 1)
            return True, window_seconds

        return False, None

    async def check_widget_limit(self, ip: str) -> tuple[bool, Optional[int]]:
        return await self.is_rate_limited(f"widget:{ip}", max_requests=20)

    async def check_panel_limit(self, user_id: str) -> tuple[bool, Optional[int]]:
        return await self.is_rate_limited(f"panel:{user_id}", max_requests=60)
