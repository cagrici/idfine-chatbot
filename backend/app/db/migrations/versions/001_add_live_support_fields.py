"""Add live support, rating, tagging, and widget enhancement fields

Revision ID: 001_live_support
Revises: None
Create Date: 2026-02-14

New columns:
- users: agent_status
- conversations: rating, rating_comment, tags, escalated_at, first_response_at
- messages: attachments, feedback, feedback_note
- widget_configs: proactive_message, proactive_delay, announcement
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "001_live_support"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──
    op.add_column(
        "users",
        sa.Column("agent_status", sa.String(20), nullable=False, server_default="offline"),
    )

    # ── conversations ──
    op.add_column(
        "conversations",
        sa.Column("rating", sa.SmallInteger(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("rating_comment", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("tags", JSONB(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── messages ──
    op.add_column(
        "messages",
        sa.Column("attachments", JSONB(), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("feedback", sa.String(10), nullable=True),
    )
    op.add_column(
        "messages",
        sa.Column("feedback_note", sa.Text(), nullable=True),
    )

    # ── widget_configs ──
    op.add_column(
        "widget_configs",
        sa.Column("proactive_message", sa.Text(), nullable=True),
    )
    op.add_column(
        "widget_configs",
        sa.Column("proactive_delay", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "widget_configs",
        sa.Column("announcement", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    # ── widget_configs ──
    op.drop_column("widget_configs", "announcement")
    op.drop_column("widget_configs", "proactive_delay")
    op.drop_column("widget_configs", "proactive_message")

    # ── messages ──
    op.drop_column("messages", "feedback_note")
    op.drop_column("messages", "feedback")
    op.drop_column("messages", "attachments")

    # ── conversations ──
    op.drop_column("conversations", "first_response_at")
    op.drop_column("conversations", "escalated_at")
    op.drop_column("conversations", "tags")
    op.drop_column("conversations", "rating_comment")
    op.drop_column("conversations", "rating")

    # ── users ──
    op.drop_column("users", "agent_status")
