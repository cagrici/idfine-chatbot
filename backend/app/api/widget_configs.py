from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import require_permission
from app.models.user import User
from app.models.widget_config import WidgetConfig
from app.schemas.widget_config import (
    WidgetConfigCreate,
    WidgetConfigListResponse,
    WidgetConfigResponse,
    WidgetConfigUpdate,
)

router = APIRouter(prefix="/admin/widget-configs", tags=["widget-configs"])


@router.get("", response_model=WidgetConfigListResponse)
async def list_widget_configs(
    user: Annotated[User, Depends(require_permission("documents.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(50, le=100),
    offset: int = Query(0, ge=0),
):
    """List all widget configurations."""
    count_query = select(func.count(WidgetConfig.id))
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        select(WidgetConfig)
        .order_by(WidgetConfig.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    configs = result.scalars().all()

    return WidgetConfigListResponse(
        configs=[WidgetConfigResponse.model_validate(c) for c in configs],
        total=total,
    )


@router.post("", response_model=WidgetConfigResponse)
async def create_widget_config(
    body: WidgetConfigCreate,
    user: Annotated[User, Depends(require_permission("documents.upload"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new widget configuration."""
    config = WidgetConfig(**body.model_dump(), created_by=user.id)
    db.add(config)
    await db.flush()
    await db.commit()
    await db.refresh(config)
    return WidgetConfigResponse.model_validate(config)


@router.get("/{config_id}", response_model=WidgetConfigResponse)
async def get_widget_config(
    config_id: UUID,
    user: Annotated[User, Depends(require_permission("documents.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a single widget configuration."""
    result = await db.execute(
        select(WidgetConfig).where(WidgetConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Widget konfigürasyonu bulunamadı")
    return WidgetConfigResponse.model_validate(config)


@router.put("/{config_id}", response_model=WidgetConfigResponse)
async def update_widget_config(
    config_id: UUID,
    body: WidgetConfigUpdate,
    user: Annotated[User, Depends(require_permission("documents.upload"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a widget configuration."""
    result = await db.execute(
        select(WidgetConfig).where(WidgetConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Widget konfigürasyonu bulunamadı")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(config, key, value)

    await db.flush()
    await db.commit()
    await db.refresh(config)
    return WidgetConfigResponse.model_validate(config)


@router.delete("/{config_id}")
async def delete_widget_config(
    config_id: UUID,
    user: Annotated[User, Depends(require_permission("documents.delete"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a widget configuration."""
    result = await db.execute(
        select(WidgetConfig).where(WidgetConfig.id == config_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Widget konfigürasyonu bulunamadı")

    await db.delete(config)
    await db.commit()
    return {"status": "ok", "message": "Widget konfigürasyonu silindi"}
