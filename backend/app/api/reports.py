from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, cast, Date, extract, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import require_permission
from app.models.canned_response import CannedResponse
from app.models.conversation import Conversation, Message
from app.models.source_group import SourceGroup
from app.models.user import User

router = APIRouter(prefix="/admin/reports", tags=["reports"])

require_stats = require_permission("stats.view")


def _date_filter(col, date_start: date | None, date_end: date | None):
    """Build date range filters for a datetime column."""
    filters = []
    if date_start:
        filters.append(col >= datetime.combine(date_start, datetime.min.time()))
    if date_end:
        filters.append(col < datetime.combine(date_end + timedelta(days=1), datetime.min.time()))
    return filters


# ── 1. Sohbet Raporu ─────────────────────────────────────────────

@router.get("/conversations")
async def report_conversations(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_start: date | None = None,
    date_end: date | None = None,
):
    """Conversation overview report."""
    df = _date_filter(Conversation.created_at, date_start, date_end)

    # Totals
    total = (await db.execute(
        select(func.count(Conversation.id)).where(*df)
    )).scalar() or 0

    active = (await db.execute(
        select(func.count(Conversation.id)).where(Conversation.status == "active", *df)
    )).scalar() or 0

    # Unique visitors
    visitors = (await db.execute(
        select(func.count(func.distinct(Conversation.visitor_id))).where(*df)
    )).scalar() or 0

    # Average messages per conversation
    avg_msgs = (await db.execute(
        select(func.avg(
            select(func.count(Message.id))
            .where(Message.conversation_id == Conversation.id)
            .correlate(Conversation)
            .scalar_subquery()
        )).where(*df)
    )).scalar()

    # Total messages
    msg_df = _date_filter(Message.created_at, date_start, date_end)
    total_msgs = (await db.execute(
        select(func.count(Message.id)).where(*msg_df)
    )).scalar() or 0

    user_msgs = (await db.execute(
        select(func.count(Message.id)).where(Message.role == "user", *msg_df)
    )).scalar() or 0

    # Daily trend
    daily = (await db.execute(
        select(
            cast(Conversation.created_at, Date).label("day"),
            func.count(Conversation.id).label("count"),
        ).where(*df).group_by("day").order_by("day")
    )).all()

    # Status distribution
    status_dist = (await db.execute(
        select(
            Conversation.status,
            func.count(Conversation.id).label("count"),
        ).where(*df).group_by(Conversation.status)
    )).all()

    # Channel distribution
    channel_dist = (await db.execute(
        select(
            Conversation.channel,
            func.count(Conversation.id).label("count"),
        ).where(*df).group_by(Conversation.channel)
    )).all()

    return {
        "summary": {
            "total_conversations": total,
            "active_conversations": active,
            "unique_visitors": visitors,
            "avg_messages_per_conv": round(float(avg_msgs), 1) if avg_msgs else 0,
            "total_messages": total_msgs,
            "user_messages": user_msgs,
        },
        "daily_trend": [{"date": str(d), "count": c} for d, c in daily],
        "status_distribution": [{"status": s, "count": c} for s, c in status_dist],
        "channel_distribution": [{"channel": ch, "count": c} for ch, c in channel_dist],
    }


# ── 2. AI Performans Raporu ──────────────────────────────────────

@router.get("/ai-performance")
async def report_ai_performance(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_start: date | None = None,
    date_end: date | None = None,
):
    """AI agent performance report."""
    df = _date_filter(Conversation.created_at, date_start, date_end)

    total = (await db.execute(
        select(func.count(Conversation.id)).where(*df)
    )).scalar() or 0

    # Conversations that stayed in AI mode (never escalated)
    ai_only = (await db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.mode == "ai",
            Conversation.assigned_agent_id.is_(None),
            *df,
        )
    )).scalar() or 0

    # Conversations escalated to human
    escalated = (await db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.assigned_agent_id.isnot(None),
            *df,
        )
    )).scalar() or 0

    # AI messages
    msg_df = _date_filter(Message.created_at, date_start, date_end)
    ai_msgs = (await db.execute(
        select(func.count(Message.id)).where(
            Message.role == "assistant", Message.sender_type == "ai", *msg_df
        )
    )).scalar() or 0

    # Token usage
    total_tokens = (await db.execute(
        select(func.sum(Message.token_count)).where(
            Message.token_count.isnot(None), *msg_df
        )
    )).scalar() or 0

    # Intent distribution
    intent_dist = (await db.execute(
        select(
            func.coalesce(Message.intent, "bilinmeyen").label("intent"),
            func.count(Message.id).label("count"),
        ).where(Message.role == "user", *msg_df)
        .group_by(Message.intent)
        .order_by(func.count(Message.id).desc())
        .limit(10)
    )).all()

    # Daily AI vs Human
    daily_mode = (await db.execute(
        select(
            cast(Conversation.created_at, Date).label("day"),
            func.count(Conversation.id).filter(
                Conversation.assigned_agent_id.is_(None)
            ).label("ai"),
            func.count(Conversation.id).filter(
                Conversation.assigned_agent_id.isnot(None)
            ).label("human"),
        ).where(*df).group_by("day").order_by("day")
    )).all()

    return {
        "summary": {
            "total_conversations": total,
            "ai_handled": ai_only,
            "escalated_to_human": escalated,
            "ai_resolution_rate": round(ai_only / total * 100, 1) if total > 0 else 0,
            "ai_messages": ai_msgs,
            "total_tokens": total_tokens,
        },
        "intent_distribution": [{"intent": i, "count": c} for i, c in intent_dist],
        "daily_mode": [{"date": str(d), "ai": a, "human": h} for d, a, h in daily_mode],
    }


# ── 3. Canlı Destek Raporu ───────────────────────────────────────

@router.get("/live-support")
async def report_live_support(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_start: date | None = None,
    date_end: date | None = None,
):
    """Live support agent performance report."""
    df = _date_filter(Conversation.created_at, date_start, date_end)

    # Total human-handled
    human_total = (await db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.assigned_agent_id.isnot(None), *df
        )
    )).scalar() or 0

    # Messages by human agents
    msg_df = _date_filter(Message.created_at, date_start, date_end)
    human_msgs = (await db.execute(
        select(func.count(Message.id)).where(
            Message.sender_type == "human", *msg_df
        )
    )).scalar() or 0

    # Agent leaderboard
    agent_stats = (await db.execute(
        select(
            User.full_name,
            func.count(func.distinct(Conversation.id)).label("conversations"),
            func.count(Message.id).label("messages"),
        )
        .join(Conversation, Conversation.assigned_agent_id == User.id)
        .outerjoin(Message, (Message.agent_id == User.id) & (Message.sender_type == "human"))
        .where(*df)
        .group_by(User.id, User.full_name)
        .order_by(func.count(func.distinct(Conversation.id)).desc())
    )).all()

    # Daily human interventions
    daily_human = (await db.execute(
        select(
            cast(Conversation.created_at, Date).label("day"),
            func.count(Conversation.id).label("count"),
        ).where(Conversation.assigned_agent_id.isnot(None), *df)
        .group_by("day").order_by("day")
    )).all()

    # SLA metrics: average first response time
    sla_result = await db.execute(
        select(
            func.avg(
                extract("epoch", Conversation.first_response_at) -
                extract("epoch", Conversation.escalated_at)
            ).label("avg_response_seconds"),
            func.count(Conversation.id).label("sla_total"),
        ).where(
            Conversation.escalated_at.isnot(None),
            Conversation.first_response_at.isnot(None),
            *df,
        )
    )
    sla_row = sla_result.first()
    avg_response_sec = round(sla_row[0] or 0, 1)
    sla_total = sla_row[1] or 0

    # SLA breach count (response > 5 minutes)
    sla_breached = (await db.execute(
        select(func.count(Conversation.id)).where(
            Conversation.escalated_at.isnot(None),
            Conversation.first_response_at.isnot(None),
            (extract("epoch", Conversation.first_response_at) - extract("epoch", Conversation.escalated_at)) > 300,
            *df,
        )
    )).scalar() or 0

    return {
        "summary": {
            "total_human_conversations": human_total,
            "total_human_messages": human_msgs,
            "active_agents": len(agent_stats),
            "avg_first_response_seconds": avg_response_sec,
            "sla_measured_conversations": sla_total,
            "sla_breached": sla_breached,
            "sla_compliance_rate": round((sla_total - sla_breached) / sla_total * 100, 1) if sla_total > 0 else 100,
        },
        "agent_leaderboard": [
            {"name": n, "conversations": c, "messages": m}
            for n, c, m in agent_stats
        ],
        "daily_trend": [{"date": str(d), "count": c} for d, c in daily_human],
    }


# ── 4. Zaman Analizi ─────────────────────────────────────────────

@router.get("/time-analysis")
async def report_time_analysis(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_start: date | None = None,
    date_end: date | None = None,
):
    """Time-based analysis: hourly and weekday patterns."""
    df = _date_filter(Conversation.created_at, date_start, date_end)

    # Hourly distribution
    hourly = (await db.execute(
        select(
            extract("hour", Conversation.created_at).label("hour"),
            func.count(Conversation.id).label("count"),
        ).where(*df).group_by("hour").order_by("hour")
    )).all()

    # Weekday distribution (0=Sun in PG, but we'll use isodow: 1=Mon..7=Sun)
    weekday = (await db.execute(
        select(
            extract("isodow", Conversation.created_at).label("dow"),
            func.count(Conversation.id).label("count"),
        ).where(*df).group_by("dow").order_by("dow")
    )).all()

    # Hourly heatmap (day_of_week x hour)
    heatmap = (await db.execute(
        select(
            extract("isodow", Conversation.created_at).label("dow"),
            extract("hour", Conversation.created_at).label("hour"),
            func.count(Conversation.id).label("count"),
        ).where(*df).group_by("dow", "hour").order_by("dow", "hour")
    )).all()

    dow_names = {1: "Pzt", 2: "Sal", 3: "Car", 4: "Per", 5: "Cum", 6: "Cmt", 7: "Paz"}

    return {
        "hourly": [{"hour": int(h), "count": c} for h, c in hourly],
        "weekday": [{"day": dow_names.get(int(d), str(d)), "day_num": int(d), "count": c} for d, c in weekday],
        "heatmap": [{"day": dow_names.get(int(d), str(d)), "day_num": int(d), "hour": int(h), "count": c} for d, h, c in heatmap],
    }


# ── 5. Kaynak Grubu Raporu ───────────────────────────────────────

@router.get("/source-groups")
async def report_source_groups(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_start: date | None = None,
    date_end: date | None = None,
):
    """Source group traffic and performance report."""
    df = _date_filter(Conversation.created_at, date_start, date_end)

    sg_stats = (await db.execute(
        select(
            func.coalesce(SourceGroup.name, "Tanimsiz").label("group_name"),
            func.count(Conversation.id).label("conversations"),
            func.count(func.distinct(Conversation.visitor_id)).label("visitors"),
            func.sum(
                select(func.count(Message.id))
                .where(Message.conversation_id == Conversation.id)
                .correlate(Conversation)
                .scalar_subquery()
            ).label("messages"),
        )
        .outerjoin(SourceGroup, Conversation.source_group_id == SourceGroup.id)
        .where(*df)
        .group_by(SourceGroup.id, SourceGroup.name)
        .order_by(func.count(Conversation.id).desc())
    )).all()

    # Daily by source group
    daily_sg = (await db.execute(
        select(
            cast(Conversation.created_at, Date).label("day"),
            func.coalesce(SourceGroup.name, "Tanimsiz").label("group_name"),
            func.count(Conversation.id).label("count"),
        )
        .outerjoin(SourceGroup, Conversation.source_group_id == SourceGroup.id)
        .where(*df)
        .group_by("day", SourceGroup.name)
        .order_by("day")
    )).all()

    return {
        "summary": [
            {"group": g, "conversations": c, "visitors": v, "messages": m or 0}
            for g, c, v, m in sg_stats
        ],
        "daily_trend": [{"date": str(d), "group": g, "count": c} for d, g, c in daily_sg],
    }


# ── 6. Hazır Yanıt Kullanım Raporu ──────────────────────────────

@router.get("/canned-responses")
async def report_canned_responses(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Canned response usage report."""
    # Top used
    top_used = (await db.execute(
        select(
            CannedResponse.title,
            CannedResponse.category,
            CannedResponse.scope,
            CannedResponse.usage_count,
            User.full_name.label("owner_name"),
        )
        .outerjoin(User, CannedResponse.owner_id == User.id)
        .where(CannedResponse.is_active == True)
        .order_by(CannedResponse.usage_count.desc())
        .limit(20)
    )).all()

    # By category
    by_cat = (await db.execute(
        select(
            CannedResponse.category,
            func.count(CannedResponse.id).label("template_count"),
            func.sum(CannedResponse.usage_count).label("total_usage"),
        )
        .where(CannedResponse.is_active == True)
        .group_by(CannedResponse.category)
        .order_by(func.sum(CannedResponse.usage_count).desc())
    )).all()

    # Total
    totals = (await db.execute(
        select(
            func.count(CannedResponse.id),
            func.sum(CannedResponse.usage_count),
        ).where(CannedResponse.is_active == True)
    )).first()

    return {
        "summary": {
            "total_templates": totals[0] or 0,
            "total_usage": totals[1] or 0,
        },
        "top_used": [
            {"title": t, "category": cat, "scope": s, "usage_count": u, "owner": o}
            for t, cat, s, u, o in top_used
        ],
        "by_category": [
            {"category": cat, "template_count": tc, "total_usage": tu or 0}
            for cat, tc, tu in by_cat
        ],
    }


# ── 7. Mesaj Analizi ─────────────────────────────────────────────

@router.get("/messages")
async def report_messages(
    user: Annotated[User, Depends(require_stats)],
    db: Annotated[AsyncSession, Depends(get_db)],
    date_start: date | None = None,
    date_end: date | None = None,
):
    """Message volume and type analysis."""
    msg_df = _date_filter(Message.created_at, date_start, date_end)

    total = (await db.execute(
        select(func.count(Message.id)).where(*msg_df)
    )).scalar() or 0

    # By role
    by_role = (await db.execute(
        select(Message.role, func.count(Message.id).label("count"))
        .where(*msg_df).group_by(Message.role)
    )).all()

    # By sender_type
    by_sender = (await db.execute(
        select(Message.sender_type, func.count(Message.id).label("count"))
        .where(*msg_df).group_by(Message.sender_type)
    )).all()

    # Daily volume
    daily = (await db.execute(
        select(
            cast(Message.created_at, Date).label("day"),
            func.count(Message.id).filter(Message.role == "user").label("user_msgs"),
            func.count(Message.id).filter(Message.role == "assistant").label("assistant_msgs"),
        ).where(*msg_df).group_by("day").order_by("day")
    )).all()

    # Token usage daily
    daily_tokens = (await db.execute(
        select(
            cast(Message.created_at, Date).label("day"),
            func.sum(Message.token_count).label("tokens"),
        ).where(Message.token_count.isnot(None), *msg_df)
        .group_by("day").order_by("day")
    )).all()

    return {
        "summary": {
            "total_messages": total,
            "by_role": {r: c for r, c in by_role},
            "by_sender_type": {s or "unknown": c for s, c in by_sender},
        },
        "daily_volume": [
            {"date": str(d), "user": u, "assistant": a}
            for d, u, a in daily
        ],
        "daily_tokens": [{"date": str(d), "tokens": t or 0} for d, t in daily_tokens],
    }
