import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import redis.asyncio as aioredis

from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.security import hash_password
from app.db.database import get_db
from app.dependencies import get_redis, has_permission, require_permission
from app.models.activity_log import ActivityLog
from app.models.conversation import Conversation, Message
from app.models.document import Document
from app.models.role import Role
from app.models.user import User
from app.schemas.auth import (
    ActivityLogResponse,
    ChangeRoleRequest,
    CreateUserRequest,
    ResetPasswordRequest,
    RoleResponse,
    UpdateUserRequest,
    UserDetailResponse,
    UserListResponse,
    UserResponse,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_user_or_404(db: AsyncSession, user_id: str) -> User:
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, TypeError):
        raise NotFoundError("Geçersiz kullanıcı ID")
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError("Kullanıcı bulunamadı")
    return user


async def _get_role(db: AsyncSession, role_name: str) -> Role | None:
    result = await db.execute(select(Role).where(Role.name == role_name))
    return result.scalar_one_or_none()


async def _log_activity(
    db: AsyncSession,
    user_id: uuid.UUID,
    action: str,
    target_type: str | None,
    target_id: str | None,
    details: dict | None,
    request: Request,
):
    log = ActivityLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details,
        ip_address=request.client.host if request.client else None,
    )
    db.add(log)


def _user_response(u: User) -> UserResponse:
    return UserResponse(
        id=str(u.id),
        email=u.email,
        full_name=u.full_name,
        role=u.role,
        is_active=u.is_active,
        last_login_at=u.last_login_at,
        created_at=u.created_at,
    )


# ── Stats ────────────────────────────────────────────────────────────────────


@router.get("/stats")
async def get_stats(
    user: Annotated[User, Depends(require_permission("stats.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get dashboard statistics."""
    conv_count = (await db.execute(select(func.count(Conversation.id)))).scalar() or 0
    msg_count = (await db.execute(select(func.count(Message.id)))).scalar() or 0
    doc_count = (await db.execute(select(func.count(Document.id)))).scalar() or 0
    user_count = (await db.execute(select(func.count(User.id)))).scalar() or 0

    active_convs = (
        await db.execute(
            select(func.count(Conversation.id)).where(
                Conversation.status == "active"
            )
        )
    ).scalar() or 0

    return {
        "total_conversations": conv_count,
        "active_conversations": active_convs,
        "total_messages": msg_count,
        "total_documents": doc_count,
        "total_users": user_count,
    }


# ── Users CRUD ───────────────────────────────────────────────────────────────


@router.get("/users", response_model=UserListResponse)
async def list_users(
    user: Annotated[User, Depends(require_permission("users.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(None),
    role: str | None = Query(None),
    is_active: bool | None = Query(None),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
):
    """List users with pagination, search, and filters."""
    query = select(User)
    count_query = select(func.count(User.id))

    if search:
        search_filter = or_(
            User.email.ilike(f"%{search}%"),
            User.full_name.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)
    if role:
        query = query.where(User.role == role)
        count_query = count_query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(User.created_at.desc()).limit(limit).offset(offset)
    )
    users = result.scalars().all()

    return UserListResponse(
        users=[_user_response(u) for u in users],
        total=total,
    )


@router.get("/users/{user_id}", response_model=UserDetailResponse)
async def get_user(
    user_id: str,
    user: Annotated[User, Depends(require_permission("users.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get single user detail."""
    target = await _get_user_or_404(db, user_id)
    return UserDetailResponse(
        id=str(target.id),
        email=target.email,
        full_name=target.full_name,
        role=target.role,
        is_active=target.is_active,
        last_login_at=target.last_login_at,
        created_at=target.created_at,
        odoo_user_id=target.odoo_user_id,
        updated_at=target.updated_at,
    )


@router.post("/users", response_model=UserResponse)
async def create_user(
    body: CreateUserRequest,
    user: Annotated[User, Depends(require_permission("users.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
):
    """Create a new user with role hierarchy enforcement."""
    # Validate role exists
    target_role = await _get_role(db, body.role)
    if not target_role:
        raise HTTPException(400, "Geçersiz rol")

    # Hierarchy check
    acting_role = await _get_role(db, user.role)
    if acting_role and target_role.level >= acting_role.level and user.role != "admin":
        raise AuthorizationError("Kendi seviyenizden yüksek rol atayamazsınız")

    # Check duplicate email
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Bu e-posta adresi zaten kayıtlı")

    new_user = User(
        email=body.email,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
        role_id=target_role.id,
    )
    db.add(new_user)
    await db.flush()

    await _log_activity(
        db, user.id, "user.create", "user", str(new_user.id),
        {"email": body.email, "role": body.role}, request,
    )
    await db.commit()

    return _user_response(new_user)


@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    user: Annotated[User, Depends(require_permission("users.edit"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
):
    """Update user profile (name, email)."""
    target = await _get_user_or_404(db, user_id)

    # Hierarchy: cannot edit user at same or higher level (unless admin)
    if user.role != "admin":
        acting_role = await _get_role(db, user.role)
        target_role = await _get_role(db, target.role)
        if acting_role and target_role and target_role.level >= acting_role.level:
            raise AuthorizationError("Bu kullanıcıyı düzenleme yetkiniz yok")

    changes = {}
    if body.email is not None and body.email != target.email:
        # Check duplicate
        existing = await db.execute(
            select(User).where(User.email == body.email, User.id != target.id)
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, "Bu e-posta adresi zaten kayıtlı")
        changes["email"] = {"old": target.email, "new": body.email}
        target.email = body.email

    if body.full_name is not None and body.full_name != target.full_name:
        changes["full_name"] = {"old": target.full_name, "new": body.full_name}
        target.full_name = body.full_name

    if changes:
        await _log_activity(
            db, user.id, "user.update", "user", user_id, changes, request,
        )
        await db.flush()
        await db.commit()

    return _user_response(target)


@router.put("/users/{user_id}/role", response_model=UserResponse)
async def change_user_role(
    user_id: str,
    body: ChangeRoleRequest,
    user: Annotated[User, Depends(require_permission("users.assign_role"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
):
    """Change user role with hierarchy enforcement."""
    target = await _get_user_or_404(db, user_id)

    if str(target.id) == str(user.id):
        raise HTTPException(400, "Kendi rolünüzü değiştiremezsiniz")

    new_role = await _get_role(db, body.role)
    if not new_role:
        raise HTTPException(400, "Geçersiz rol")

    if user.role != "admin":
        acting_role = await _get_role(db, user.role)
        target_current_role = await _get_role(db, target.role)

        if acting_role and target_current_role and target_current_role.level >= acting_role.level:
            raise AuthorizationError("Bu kullanıcının rolünü değiştiremezsiniz")
        if acting_role and new_role.level >= acting_role.level:
            raise AuthorizationError("Kendi seviyenizden yüksek rol atayamazsınız")

    old_role = target.role
    target.role = body.role
    target.role_id = new_role.id

    await _log_activity(
        db, user.id, "user.change_role", "user", user_id,
        {"old_role": old_role, "new_role": body.role}, request,
    )
    await db.flush()
    await db.commit()

    return _user_response(target)


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    body: ResetPasswordRequest,
    user: Annotated[User, Depends(require_permission("users.reset_password"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
):
    """Admin resets another user's password."""
    target = await _get_user_or_404(db, user_id)

    if target.role == "admin" and user.role != "admin":
        raise AuthorizationError("Admin parolasını sıfırlama yetkiniz yok")

    target.password_hash = hash_password(body.new_password)

    await _log_activity(
        db, user.id, "user.reset_password", "user", user_id, {}, request,
    )
    await db.flush()
    await db.commit()

    return {"status": "ok", "message": "Parola sıfırlandı"}


@router.put("/users/{user_id}/toggle-active", response_model=UserResponse)
async def toggle_user_active(
    user_id: str,
    user: Annotated[User, Depends(require_permission("users.delete"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    request: Request,
):
    """Toggle user active/inactive status."""
    target = await _get_user_or_404(db, user_id)

    if str(target.id) == str(user.id):
        raise HTTPException(400, "Kendinizi deaktif edemezsiniz")

    if target.role == "admin" and user.role != "admin":
        raise AuthorizationError("Admin kullanıcısını deaktif etme yetkiniz yok")

    target.is_active = not target.is_active

    await _log_activity(
        db, user.id, "user.toggle_active", "user", user_id,
        {"is_active": target.is_active}, request,
    )
    await db.flush()
    await db.commit()

    return _user_response(target)


# ── Roles ────────────────────────────────────────────────────────────────────


@router.get("/roles", response_model=list[RoleResponse])
async def list_roles(
    user: Annotated[User, Depends(require_permission("users.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all available roles."""
    result = await db.execute(select(Role).order_by(Role.level.desc()))
    roles = result.scalars().all()
    return [
        RoleResponse(
            id=str(r.id),
            name=r.name,
            display_name=r.display_name,
            description=r.description,
            permissions=r.permissions or {},
            is_system=r.is_system,
            level=r.level,
        )
        for r in roles
    ]


# ── Activity Logs ────────────────────────────────────────────────────────────


@router.get("/activity-logs")
async def list_activity_logs(
    user: Annotated[User, Depends(require_permission("users.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
):
    """List recent activity logs."""
    result = await db.execute(
        select(ActivityLog)
        .order_by(ActivityLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    logs = result.scalars().all()

    # Batch load user info for display
    user_ids = list({log.user_id for log in logs})
    user_map = {}
    if user_ids:
        users_result = await db.execute(
            select(User).where(User.id.in_(user_ids))
        )
        for u in users_result.scalars().all():
            user_map[u.id] = u

    return {
        "logs": [
            ActivityLogResponse(
                id=log.id,
                user_id=str(log.user_id),
                action=log.action,
                target_type=log.target_type,
                target_id=log.target_id,
                details=log.details,
                ip_address=log.ip_address,
                created_at=log.created_at,
                user_email=user_map.get(log.user_id, None) and user_map[log.user_id].email,
                user_full_name=user_map.get(log.user_id, None) and user_map[log.user_id].full_name,
            )
            for log in logs
        ]
    }


# ── Blacklist ─────────────────────────────────────────────────

require_admin = require_permission("admin.full_access")


@router.get("/blacklist")
async def list_blacklist(
    user: Annotated[User, Depends(require_admin)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """List all blacklisted IPs and visitors."""
    from app.services.blacklist_service import BlacklistService
    bl = BlacklistService(redis_client)
    entries = await bl.list_all()
    return {"entries": entries, "total": len(entries)}


@router.post("/blacklist")
async def add_to_blacklist(
    request: Request,
    user: Annotated[User, Depends(require_admin)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """Add an IP or visitor to the blacklist."""
    from app.services.blacklist_service import BlacklistService
    body = await request.json()
    entry_type = body.get("type", "ip")  # "ip" or "visitor"
    value = body.get("value", "").strip()
    reason = body.get("reason", "")

    if not value:
        raise HTTPException(status_code=400, detail="Value is required")
    if entry_type not in ("ip", "visitor"):
        raise HTTPException(status_code=400, detail="Type must be 'ip' or 'visitor'")

    bl = BlacklistService(redis_client)
    await bl.add(entry_type, value, reason, user.full_name)
    return {"status": "ok", "type": entry_type, "value": value}


@router.delete("/blacklist")
async def remove_from_blacklist(
    request: Request,
    user: Annotated[User, Depends(require_admin)],
    redis_client: Annotated[aioredis.Redis, Depends(get_redis)],
):
    """Remove an entry from the blacklist."""
    from app.services.blacklist_service import BlacklistService
    body = await request.json()
    entry_type = body.get("type", "ip")
    value = body.get("value", "").strip()

    if not value:
        raise HTTPException(status_code=400, detail="Value is required")

    bl = BlacklistService(redis_client)
    await bl.remove(entry_type, value)
    return {"status": "ok", "type": entry_type, "value": value}
