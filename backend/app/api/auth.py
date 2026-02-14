from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.database import get_db
from app.dependencies import get_current_user
from app.models.role import Role
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class MeResponse(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    is_active: bool
    agent_status: str = "offline"
    permissions: list[str] = []
    last_login_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ChangeOwnPasswordRequest(BaseModel):
    current_password: str
    new_password: str


class UpdateProfileRequest(BaseModel):
    full_name: str | None = None
    email: str | None = None


def _get_permission_keys(role_obj: Role | None) -> list[str]:
    if not role_obj or not role_obj.permissions:
        return []
    return [k for k, v in role_obj.permissions.items() if v]


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise AuthenticationError("Geçersiz email veya şifre")

    if not user.is_active:
        raise AuthenticationError("Hesabınız devre dışı bırakılmış")

    # Load role permissions for JWT
    role_result = await db.execute(select(Role).where(Role.name == user.role))
    role_obj = role_result.scalar_one_or_none()
    permissions = _get_permission_keys(role_obj)

    # Update last login
    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    await db.commit()

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role, permissions),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise AuthenticationError("Geçersiz refresh token")

    user_id = payload.get("sub")
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise AuthenticationError("Kullanıcı bulunamadı")

    # Load permissions for new token
    role_result = await db.execute(select(Role).where(Role.name == user.role))
    role_obj = role_result.scalar_one_or_none()
    permissions = _get_permission_keys(role_obj)

    return TokenResponse(
        access_token=create_access_token(str(user.id), user.role, permissions),
        refresh_token=create_refresh_token(str(user.id)),
    )


@router.get("/me", response_model=MeResponse)
async def get_me(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    role_result = await db.execute(select(Role).where(Role.name == user.role))
    role_obj = role_result.scalar_one_or_none()
    permissions = _get_permission_keys(role_obj)

    return MeResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        agent_status=user.agent_status or "offline",
        permissions=permissions,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.put("/me/password")
async def change_own_password(
    body: ChangeOwnPasswordRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    if not verify_password(body.current_password, user.password_hash):
        raise AuthenticationError("Mevcut parola yanlış")

    if len(body.new_password) < 6:
        raise AuthenticationError("Yeni parola en az 6 karakter olmalı")

    user.password_hash = hash_password(body.new_password)
    await db.flush()
    await db.commit()

    return {"status": "ok", "message": "Parola değiştirildi"}


@router.put("/me/profile", response_model=MeResponse)
async def update_own_profile(
    body: UpdateProfileRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update own profile (name, email)."""
    changed = False

    if body.full_name is not None and body.full_name.strip() != user.full_name:
        user.full_name = body.full_name.strip()
        changed = True

    if body.email is not None and body.email.strip() != user.email:
        # Check duplicate
        existing = await db.execute(
            select(User).where(User.email == body.email.strip(), User.id != user.id)
        )
        if existing.scalar_one_or_none():
            raise AuthenticationError("Bu e-posta adresi zaten kayıtlı")
        user.email = body.email.strip()
        changed = True

    if changed:
        await db.flush()
        await db.commit()

    role_result = await db.execute(select(Role).where(Role.name == user.role))
    role_obj = role_result.scalar_one_or_none()
    permissions = _get_permission_keys(role_obj)

    return MeResponse(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        agent_status=user.agent_status or "offline",
        permissions=permissions,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


VALID_AGENT_STATUSES = ("online", "busy", "away", "offline")


class AgentStatusRequest(BaseModel):
    status: str = "online"


@router.put("/me/status")
async def update_agent_status(
    body: AgentStatusRequest,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update agent availability status."""
    status = body.status
    if status not in VALID_AGENT_STATUSES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Status must be one of: {', '.join(VALID_AGENT_STATUSES)}")

    user.agent_status = status
    await db.commit()
    return {"status": status, "agent_id": str(user.id)}


@router.get("/agents/status")
async def get_agents_status(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get all agents with their current status."""
    result = await db.execute(
        select(User.id, User.full_name, User.agent_status, User.role)
        .where(User.is_active == True)
        .where(User.role.in_(["admin", "agent", "manager"]))
        .order_by(User.full_name)
    )
    agents = [
        {
            "id": str(row.id),
            "full_name": row.full_name,
            "status": row.agent_status or "offline",
            "role": row.role,
        }
        for row in result.all()
    ]
    return {"agents": agents}
