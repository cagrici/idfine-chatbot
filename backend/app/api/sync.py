"""Admin API endpoints for Odoo product sync management."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import require_admin
from app.models.audit import OdooSyncLog
from app.models.user import User

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/trigger")
async def trigger_sync(
    user: Annotated[User, Depends(require_admin)],
    mode: str = Query("delta", pattern="^(delta|full)$"),
):
    """Manually trigger a product sync (admin only)."""
    from app.services.scheduler import scheduler

    task_name = f"odoo_{mode}_sync"
    try:
        await scheduler.run_now(task_name)
        return {"status": "ok", "message": f"{mode} sync completed"}
    except ValueError:
        return {"status": "error", "message": f"Sync task '{task_name}' not registered. Is sync enabled?"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/status")
async def sync_status(
    user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get the latest sync status for each sync type."""
    result = {}
    for sync_type in ("delta", "full"):
        stmt = (
            select(OdooSyncLog)
            .where(OdooSyncLog.sync_type == sync_type)
            .order_by(OdooSyncLog.started_at.desc())
            .limit(1)
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
        if row:
            result[sync_type] = {
                "id": row.id,
                "status": row.status,
                "records_synced": row.records_synced,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "error_message": row.error_message,
            }
        else:
            result[sync_type] = None

    return result


@router.get("/logs")
async def sync_logs(
    user: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, ge=1, le=100),
):
    """Get recent sync log entries."""
    stmt = (
        select(OdooSyncLog)
        .order_by(OdooSyncLog.started_at.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    logs = result.scalars().all()

    return [
        {
            "id": log.id,
            "sync_type": log.sync_type,
            "status": log.status,
            "records_synced": log.records_synced,
            "error_message": log.error_message,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        }
        for log in logs
    ]
