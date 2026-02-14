import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class WidgetConfig(Base):
    __tablename__ = "widget_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(500), nullable=False)
    source_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_groups.id"), nullable=True, index=True
    )
    logo_variant: Mapped[str] = mapped_column(String(10), default="dark")
    brand_color: Mapped[str] = mapped_column(String(20), default="#231f20")
    brand_name: Mapped[str] = mapped_column(String(100), default="idfine")
    welcome_message: Mapped[str] = mapped_column(
        Text, default="Merhaba! Size nasıl yardımcı olabilirim?"
    )
    placeholder: Mapped[str] = mapped_column(
        String(200), default="Mesajınızı yazın..."
    )
    position: Mapped[str] = mapped_column(String(20), default="bottom-right")
    width: Mapped[int] = mapped_column(Integer, default=380)
    height: Mapped[int] = mapped_column(Integer, default=560)
    trigger_size: Mapped[int] = mapped_column(Integer, default=60)
    proactive_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    proactive_delay: Mapped[int] = mapped_column(Integer, default=0)  # seconds, 0=disabled
    announcement: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    source_group: Mapped["SourceGroup | None"] = relationship(back_populates="widget_configs")
