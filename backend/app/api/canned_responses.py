import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.database import get_db
from app.dependencies import get_current_user, has_permission
from app.models.canned_response import CannedResponse
from app.models.user import User
from app.schemas.canned_response import (
    CannedResponseCreate,
    CannedResponseResponse,
    CannedResponseUpdate,
)

router = APIRouter(prefix="/canned-responses", tags=["canned-responses"])


def _to_response(cr: CannedResponse, owner_name: str | None = None) -> CannedResponseResponse:
    return CannedResponseResponse(
        id=str(cr.id),
        title=cr.title,
        content=cr.content,
        category=cr.category,
        scope=cr.scope,
        shortcut=cr.shortcut,
        owner_id=str(cr.owner_id),
        owner_name=owner_name,
        is_active=cr.is_active,
        usage_count=cr.usage_count,
        created_at=cr.created_at,
        updated_at=cr.updated_at,
    )


@router.get("", response_model=list[CannedResponseResponse])
async def list_canned_responses(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    scope: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = Query(default=100, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List canned responses: all global + user's personal."""
    query = select(CannedResponse, User.full_name).outerjoin(
        User, CannedResponse.owner_id == User.id
    ).where(CannedResponse.is_active == True)

    # Show global + user's own personal
    if scope == "global":
        query = query.where(CannedResponse.scope == "global")
    elif scope == "personal":
        query = query.where(
            CannedResponse.scope == "personal",
            CannedResponse.owner_id == user.id,
        )
    else:
        query = query.where(
            or_(
                CannedResponse.scope == "global",
                (CannedResponse.scope == "personal") & (CannedResponse.owner_id == user.id),
            )
        )

    if category:
        query = query.where(CannedResponse.category == category)

    if q:
        pattern = f"%{q}%"
        query = query.where(
            or_(
                CannedResponse.title.ilike(pattern),
                CannedResponse.content.ilike(pattern),
            )
        )

    query = query.order_by(CannedResponse.usage_count.desc(), CannedResponse.title)
    query = query.limit(limit).offset(offset)

    result = await db.execute(query)
    rows = result.all()

    return [_to_response(cr, owner_name) for cr, owner_name in rows]


@router.post("", response_model=CannedResponseResponse)
async def create_canned_response(
    body: CannedResponseCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new canned response."""
    # Global scope requires canned_responses.manage permission
    if body.scope == "global" and not has_permission(user, "canned_responses.manage"):
        raise AuthorizationError("Genel sablon olusturmak icin yetkiniz yok")

    cr = CannedResponse(
        title=body.title,
        content=body.content,
        category=body.category,
        scope=body.scope,
        shortcut=body.shortcut,
        owner_id=user.id,
    )
    db.add(cr)
    await db.commit()
    await db.refresh(cr)

    return _to_response(cr, user.full_name)


@router.put("/{response_id}", response_model=CannedResponseResponse)
async def update_canned_response(
    response_id: str,
    body: CannedResponseUpdate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a canned response."""
    result = await db.execute(
        select(CannedResponse).where(CannedResponse.id == uuid.UUID(response_id))
    )
    cr = result.scalar_one_or_none()
    if not cr:
        raise NotFoundError("Sablon bulunamadi")

    # Only owner or users with manage permission can edit
    if cr.owner_id != user.id and not has_permission(user, "canned_responses.manage"):
        raise AuthorizationError("Bu sablonu duzenleme yetkiniz yok")

    if body.title is not None:
        cr.title = body.title
    if body.content is not None:
        cr.content = body.content
    if body.category is not None:
        cr.category = body.category
    if body.shortcut is not None:
        cr.shortcut = body.shortcut
    if body.is_active is not None:
        cr.is_active = body.is_active

    await db.commit()
    await db.refresh(cr)

    # Get owner name
    owner_result = await db.execute(select(User.full_name).where(User.id == cr.owner_id))
    owner_name = owner_result.scalar()

    return _to_response(cr, owner_name)


@router.delete("/{response_id}")
async def delete_canned_response(
    response_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a canned response."""
    result = await db.execute(
        select(CannedResponse).where(CannedResponse.id == uuid.UUID(response_id))
    )
    cr = result.scalar_one_or_none()
    if not cr:
        raise NotFoundError("Sablon bulunamadi")

    if cr.owner_id != user.id and not has_permission(user, "canned_responses.manage"):
        raise AuthorizationError("Bu sablonu silme yetkiniz yok")

    await db.delete(cr)
    await db.commit()

    return {"status": "deleted"}


@router.post("/{response_id}/use")
async def use_canned_response(
    response_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Increment usage count for a canned response."""
    result = await db.execute(
        select(CannedResponse).where(CannedResponse.id == uuid.UUID(response_id))
    )
    cr = result.scalar_one_or_none()
    if not cr:
        raise NotFoundError("Sablon bulunamadi")

    cr.usage_count = (cr.usage_count or 0) + 1
    await db.commit()

    return {"usage_count": cr.usage_count}
