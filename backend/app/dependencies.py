import uuid
from typing import Annotated

import redis.asyncio as redis
from fastapi import Depends, Header, Request
from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.rate_limiter import RateLimiter
from app.core.security import decode_token
from app.db.database import get_db
from app.models.user import User


async def get_redis(
    settings: Annotated[Settings, Depends(get_settings)],
) -> redis.Redis:
    return redis.from_url(settings.redis_url, decode_responses=True)


async def get_qdrant(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncQdrantClient:
    return AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


async def get_rate_limiter(
    redis_client: Annotated[redis.Redis, Depends(get_redis)],
) -> RateLimiter:
    return RateLimiter(redis_client)


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthenticationError("Token bulunamadı")

    token = authorization.removeprefix("Bearer ")
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise AuthenticationError("Geçersiz token")

    user_id = payload.get("sub")
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise AuthenticationError("Geçersiz token")

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise AuthenticationError("Kullanıcı bulunamadı veya aktif değil")

    return user


def has_permission(user: User, permission: str) -> bool:
    """Check if user has a specific permission."""
    if user.role_ref and user.role_ref.permissions:
        perms = user.role_ref.permissions
    else:
        return user.role == "admin"
    if perms.get("admin.full_access"):
        return True
    return perms.get(permission, False)


def require_permission(*permissions: str):
    """Factory that returns a FastAPI dependency requiring ANY of the listed permissions."""
    async def _check(
        user: Annotated[User, Depends(get_current_user)],
    ) -> User:
        for perm in permissions:
            if has_permission(user, perm):
                return user
        raise AuthorizationError(
            f"Bu işlem için yetkiniz yok"
        )
    return _check


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if user.role != "admin" and not has_permission(user, "admin.full_access"):
        raise AuthorizationError("Bu işlem için admin yetkisi gerekli")
    return user


_connection_manager = None


async def get_connection_manager():
    """Get or create the singleton ConnectionManager."""
    global _connection_manager
    if _connection_manager is None:
        from app.services.connection_manager import ConnectionManager
        settings = get_settings()
        redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        _connection_manager = ConnectionManager(redis_client)
    return _connection_manager


async def get_visitor_id(request: Request) -> str:
    """Generate or retrieve a visitor ID for anonymous widget users."""
    visitor_id = request.headers.get("X-Visitor-ID")
    if not visitor_id:
        visitor_id = str(uuid.uuid4())
    return visitor_id
