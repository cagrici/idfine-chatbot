"""Social media management API endpoints for admin panel."""
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, cast, Date, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.database import get_db
from app.dependencies import require_permission
from app.models.conversation import Conversation, Message
from app.models.user import User

router = APIRouter(prefix="/admin/social-media", tags=["social-media"])

require_stats = require_permission("stats.view")

SOCIAL_CHANNELS = ("whatsapp", "instagram", "messenger")
CHANNEL_LABELS = {
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "messenger": "Messenger",
    "widget": "Web Widget",
}


def _date_filter(col, date_start: date | None, date_end: date | None):
    filters = []
    if date_start:
        filters.append(col >= datetime.combine(date_start, datetime.min.time()))
    if date_end:
        filters.append(col < datetime.combine(date_end + timedelta(days=1), datetime.min.time()))
    return filters


# ── Dashboard Stats ─────────────────────────────────────────────


@router.get("/dashboard")
async def social_media_dashboard(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_start: date | None = None,
    date_end: date | None = None,
):
    """Social media dashboard with channel-specific stats."""
    if not date_start:
        date_start = (datetime.now() - timedelta(days=30)).date()
    if not date_end:
        date_end = datetime.now().date()

    df = _date_filter(Conversation.created_at, date_start, date_end)
    msg_df = _date_filter(Message.created_at, date_start, date_end)

    # Per-channel conversation counts
    channel_stats = (await db.execute(
        select(
            Conversation.channel,
            func.count(Conversation.id).label("conversations"),
            func.count(func.distinct(Conversation.visitor_id)).label("unique_visitors"),
        ).where(*df)
        .group_by(Conversation.channel)
    )).all()

    # Per-channel message counts
    channel_msgs = (await db.execute(
        select(
            Conversation.channel,
            func.count(Message.id).label("messages"),
        )
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(*msg_df)
        .group_by(Conversation.channel)
    )).all()
    msg_map = {ch: c for ch, c in channel_msgs}

    # Per-channel mode breakdown (AI vs Human)
    channel_modes = (await db.execute(
        select(
            Conversation.channel,
            func.count(Conversation.id).filter(
                Conversation.assigned_agent_id.is_(None)
            ).label("ai_handled"),
            func.count(Conversation.id).filter(
                Conversation.assigned_agent_id.isnot(None)
            ).label("human_handled"),
        ).where(*df)
        .group_by(Conversation.channel)
    )).all()
    mode_map = {ch: {"ai": a, "human": h} for ch, a, h in channel_modes}

    # Daily trend per channel
    daily_channel = (await db.execute(
        select(
            cast(Conversation.created_at, Date).label("day"),
            Conversation.channel,
            func.count(Conversation.id).label("count"),
        ).where(*df)
        .group_by("day", Conversation.channel)
        .order_by("day")
    )).all()

    # Today's stats
    today = datetime.now().date()
    today_filter = _date_filter(Conversation.created_at, today, today)
    today_stats = (await db.execute(
        select(
            Conversation.channel,
            func.count(Conversation.id).label("count"),
        ).where(*today_filter)
        .group_by(Conversation.channel)
    )).all()
    today_map = {ch: c for ch, c in today_stats}

    # Average response time per channel
    avg_response = (await db.execute(
        select(
            Conversation.channel,
            func.avg(
                func.extract("epoch", Conversation.first_response_at) -
                func.extract("epoch", Conversation.escalated_at)
            ).label("avg_seconds"),
        ).where(
            Conversation.escalated_at.isnot(None),
            Conversation.first_response_at.isnot(None),
            *df,
        )
        .group_by(Conversation.channel)
    )).all()
    resp_map = {ch: round(s or 0, 1) for ch, s in avg_response}

    # Recent social conversations (last 10)
    recent_social = (await db.execute(
        select(
            Conversation.id,
            Conversation.channel,
            Conversation.visitor_id,
            Conversation.status,
            Conversation.mode,
            Conversation.metadata_,
            Conversation.created_at,
            Conversation.updated_at,
        )
        .where(Conversation.channel.in_(SOCIAL_CHANNELS))
        .order_by(Conversation.updated_at.desc())
        .limit(10)
    )).all()

    channels = {}
    for ch, convs, visitors in channel_stats:
        modes = mode_map.get(ch, {"ai": 0, "human": 0})
        channels[ch] = {
            "label": CHANNEL_LABELS.get(ch, ch),
            "conversations": convs,
            "unique_visitors": visitors,
            "messages": msg_map.get(ch, 0),
            "ai_handled": modes["ai"],
            "human_handled": modes["human"],
            "today": today_map.get(ch, 0),
            "avg_response_seconds": resp_map.get(ch, 0),
        }

    # Ensure all social channels are present
    for ch in SOCIAL_CHANNELS:
        if ch not in channels:
            channels[ch] = {
                "label": CHANNEL_LABELS[ch],
                "conversations": 0,
                "unique_visitors": 0,
                "messages": 0,
                "ai_handled": 0,
                "human_handled": 0,
                "today": 0,
                "avg_response_seconds": 0,
            }

    # Totals for social only
    social_total = sum(
        channels[ch]["conversations"] for ch in SOCIAL_CHANNELS
    )
    social_msgs = sum(
        channels[ch]["messages"] for ch in SOCIAL_CHANNELS
    )
    social_visitors = sum(
        channels[ch]["unique_visitors"] for ch in SOCIAL_CHANNELS
    )
    social_today = sum(
        channels[ch]["today"] for ch in SOCIAL_CHANNELS
    )

    return {
        "summary": {
            "total_social_conversations": social_total,
            "total_social_messages": social_msgs,
            "total_social_visitors": social_visitors,
            "social_conversations_today": social_today,
        },
        "channels": channels,
        "daily_trend": [
            {"date": str(d), "channel": ch, "count": c}
            for d, ch, c in daily_channel
        ],
        "recent_social": [
            {
                "id": str(r[0]),
                "channel": r[1],
                "visitor_id": r[2],
                "status": r[3],
                "mode": r[4],
                "contact_name": (r[5] or {}).get("contact_name", ""),
                "created_at": r[6].isoformat() if r[6] else None,
                "updated_at": r[7].isoformat() if r[7] else None,
            }
            for r in recent_social
        ],
    }


# ── Channel Configuration Status ──────────────────────────────


@router.get("/channels")
async def get_channel_configs(
    user: Annotated[User, Depends(require_stats)],
):
    """Get configuration status for each social media channel."""
    settings = get_settings()

    channels = {
        "whatsapp": {
            "label": "WhatsApp Business",
            "configured": bool(settings.meta_whatsapp_phone_number_id and settings.meta_whatsapp_access_token),
            "phone_number_id": _mask(settings.meta_whatsapp_phone_number_id),
            "access_token": _mask_token(settings.meta_whatsapp_access_token),
            "api_version": settings.meta_graph_api_version,
        },
        "messenger": {
            "label": "Facebook Messenger",
            "configured": bool(settings.meta_page_access_token and settings.meta_page_id),
            "page_id": _mask(settings.meta_page_id),
            "access_token": _mask_token(settings.meta_page_access_token),
            "api_version": settings.meta_graph_api_version,
        },
        "instagram": {
            "label": "Instagram Direct",
            "configured": bool(settings.meta_page_access_token),
            "access_token": _mask_token(settings.meta_page_access_token),
            "api_version": settings.meta_graph_api_version,
            "note": "Instagram DM, Messenger ile ayni token'i kullanir.",
        },
        "webhook": {
            "url": f"https://{settings.cors_origins.split(',')[0].replace('http://', '').replace('https://', '')}/webhooks/meta" if settings.cors_origins else "/webhooks/meta",
            "verify_token": _mask(settings.meta_verify_token),
            "app_secret": _mask_token(settings.meta_app_secret),
        },
    }

    return channels


@router.get("/channel-report")
async def channel_report(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
    channel: str = "whatsapp",
    date_start: date | None = None,
    date_end: date | None = None,
):
    """Detailed report for a specific channel."""
    if not date_start:
        date_start = (datetime.now() - timedelta(days=30)).date()
    if not date_end:
        date_end = datetime.now().date()

    df = _date_filter(Conversation.created_at, date_start, date_end)
    ch_filter = [Conversation.channel == channel]

    # Basic stats
    total = (await db.execute(
        select(func.count(Conversation.id)).where(*df, *ch_filter)
    )).scalar() or 0

    active = (await db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.status == "active", *df, *ch_filter
        )
    )).scalar() or 0

    visitors = (await db.execute(
        select(func.count(func.distinct(Conversation.visitor_id))).where(*df, *ch_filter)
    )).scalar() or 0

    # Messages
    msg_df = _date_filter(Message.created_at, date_start, date_end)
    total_msgs = (await db.execute(
        select(func.count(Message.id))
        .join(Conversation, Message.conversation_id == Conversation.id)
        .where(*msg_df, *ch_filter)
    )).scalar() or 0

    # AI vs Human
    ai_handled = (await db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.assigned_agent_id.is_(None), *df, *ch_filter
        )
    )).scalar() or 0

    # Daily trend
    daily = (await db.execute(
        select(
            cast(Conversation.created_at, Date).label("day"),
            func.count(Conversation.id).label("count"),
        ).where(*df, *ch_filter)
        .group_by("day").order_by("day")
    )).all()

    # Hourly distribution
    hourly = (await db.execute(
        select(
            func.extract("hour", Conversation.created_at).label("hour"),
            func.count(Conversation.id).label("count"),
        ).where(*df, *ch_filter)
        .group_by("hour").order_by("hour")
    )).all()

    # Top visitors by message count
    top_visitors = (await db.execute(
        select(
            Conversation.visitor_id,
            Conversation.metadata_,
            func.count(Conversation.id).label("conversations"),
        ).where(*df, *ch_filter)
        .group_by(Conversation.visitor_id, Conversation.metadata_)
        .order_by(func.count(Conversation.id).desc())
        .limit(10)
    )).all()

    return {
        "channel": channel,
        "label": CHANNEL_LABELS.get(channel, channel),
        "summary": {
            "total_conversations": total,
            "active_conversations": active,
            "unique_visitors": visitors,
            "total_messages": total_msgs,
            "ai_handled": ai_handled,
            "human_handled": total - ai_handled,
            "ai_rate": round(ai_handled / total * 100, 1) if total > 0 else 0,
        },
        "daily_trend": [{"date": str(d), "count": c} for d, c in daily],
        "hourly": [{"hour": int(h), "count": c} for h, c in hourly],
        "top_visitors": [
            {
                "visitor_id": v,
                "contact_name": (m or {}).get("contact_name", ""),
                "conversations": c,
            }
            for v, m, c in top_visitors
        ],
    }


# ── Helpers ─────────────────────────────────────────────────────


def _mask(value: str) -> str:
    """Mask a config value, showing only first/last chars."""
    if not value:
        return ""
    if len(value) <= 6:
        return value[:2] + "***"
    return value[:3] + "***" + value[-3:]


def _mask_token(value: str) -> str:
    """Mask a token, showing only first few chars."""
    if not value:
        return ""
    return value[:8] + "***" + value[-4:] if len(value) > 12 else "***"
