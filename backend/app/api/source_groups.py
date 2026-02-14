from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import require_permission
from app.models.document import Document
from app.models.source_group import SourceGroup
from app.models.user import User
from app.models.widget_config import WidgetConfig
from app.schemas.source_group import (
    SourceGroupCreate,
    SourceGroupListResponse,
    SourceGroupResponse,
    SourceGroupUpdate,
)

router = APIRouter(prefix="/admin/source-groups", tags=["source-groups"])


async def _build_response(db: AsyncSession, sg: SourceGroup) -> SourceGroupResponse:
    """Build response with document and widget counts."""
    doc_count = (await db.execute(
        select(func.count(Document.id)).where(Document.source_group_id == sg.id)
    )).scalar() or 0

    wc_count = (await db.execute(
        select(func.count(WidgetConfig.id)).where(WidgetConfig.source_group_id == sg.id)
    )).scalar() or 0

    return SourceGroupResponse(
        id=sg.id,
        name=sg.name,
        slug=sg.slug,
        description=sg.description,
        color=sg.color,
        data_permissions=sg.data_permissions,
        is_default=sg.is_default,
        is_active=sg.is_active,
        document_count=doc_count,
        widget_count=wc_count,
        created_at=sg.created_at,
        updated_at=sg.updated_at,
    )


@router.get("", response_model=SourceGroupListResponse)
async def list_source_groups(
    user: Annotated[User, Depends(require_permission("source_groups.view", "documents.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """List all source groups."""
    result = await db.execute(
        select(SourceGroup).order_by(SourceGroup.created_at)
    )
    groups = result.scalars().all()

    responses = []
    for sg in groups:
        responses.append(await _build_response(db, sg))

    return SourceGroupListResponse(groups=responses, total=len(responses))


@router.post("", response_model=SourceGroupResponse, status_code=201)
async def create_source_group(
    body: SourceGroupCreate,
    user: Annotated[User, Depends(require_permission("source_groups.create"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new source group."""
    # Check uniqueness
    existing = await db.execute(
        select(SourceGroup).where(
            (SourceGroup.slug == body.slug) | (SourceGroup.name == body.name)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Bu isim veya slug zaten kullanılıyor")

    sg = SourceGroup(**body.model_dump())
    db.add(sg)
    await db.flush()
    await db.commit()
    await db.refresh(sg)
    return await _build_response(db, sg)


@router.get("/{group_id}", response_model=SourceGroupResponse)
async def get_source_group(
    group_id: UUID,
    user: Annotated[User, Depends(require_permission("source_groups.view", "documents.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get a single source group."""
    result = await db.execute(
        select(SourceGroup).where(SourceGroup.id == group_id)
    )
    sg = result.scalar_one_or_none()
    if not sg:
        raise HTTPException(404, "Kaynak grubu bulunamadı")
    return await _build_response(db, sg)


@router.put("/{group_id}", response_model=SourceGroupResponse)
async def update_source_group(
    group_id: UUID,
    body: SourceGroupUpdate,
    user: Annotated[User, Depends(require_permission("source_groups.edit"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a source group."""
    result = await db.execute(
        select(SourceGroup).where(SourceGroup.id == group_id)
    )
    sg = result.scalar_one_or_none()
    if not sg:
        raise HTTPException(404, "Kaynak grubu bulunamadı")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(sg, key, value)

    await db.flush()
    await db.commit()
    await db.refresh(sg)
    return await _build_response(db, sg)


@router.delete("/{group_id}")
async def delete_source_group(
    group_id: UUID,
    user: Annotated[User, Depends(require_permission("source_groups.delete"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a source group (only if no documents or widgets attached)."""
    result = await db.execute(
        select(SourceGroup).where(SourceGroup.id == group_id)
    )
    sg = result.scalar_one_or_none()
    if not sg:
        raise HTTPException(404, "Kaynak grubu bulunamadı")

    if sg.is_default:
        raise HTTPException(400, "Varsayılan kaynak grubu silinemez")

    # Check for attached documents
    doc_count = (await db.execute(
        select(func.count(Document.id)).where(Document.source_group_id == group_id)
    )).scalar() or 0
    if doc_count:
        raise HTTPException(
            409, f"Bu gruba ait {doc_count} doküman var. Önce dokümanları taşıyın veya silin."
        )

    # Check for attached widgets
    wc_count = (await db.execute(
        select(func.count(WidgetConfig.id)).where(WidgetConfig.source_group_id == group_id)
    )).scalar() or 0
    if wc_count:
        raise HTTPException(
            409, f"Bu gruba ait {wc_count} widget var. Önce widget'ları taşıyın veya silin."
        )

    await db.delete(sg)
    await db.commit()
    return {"status": "ok", "message": "Kaynak grubu silindi"}
