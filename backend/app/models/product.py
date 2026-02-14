from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, Index, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    urun_kodu: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    urun_tanimi: Mapped[str | None] = mapped_column(Text, nullable=True)
    marka: Mapped[str | None] = mapped_column(String(255), nullable=True)
    koleksiyon: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    urun_tipi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ebat_cm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    hacim_cc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    materyal: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ana_renk: Mapped[str | None] = mapped_column(String(255), nullable=True)
    renk_tonu: Mapped[str | None] = mapped_column(String(255), nullable=True)
    form: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stil: Mapped[str | None] = mapped_column(String(255), nullable=True)
    yuzey_bitisi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dekor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segment: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fiyat_segmenti: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kullanim_alani: Mapped[str | None] = mapped_column(String(255), nullable=True)
    servis_tipi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mutfak_uyumu: Mapped[str | None] = mapped_column(String(255), nullable=True)
    yemek_onerileri: Mapped[str | None] = mapped_column(Text, nullable=True)
    konsept_etiketler: Mapped[str | None] = mapped_column(Text, nullable=True)
    rekabet_avantaji: Mapped[str | None] = mapped_column(Text, nullable=True)
    istiflenebilirlik: Mapped[str | None] = mapped_column(String(255), nullable=True)
    kenar_citlama_dayanimi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cizilme_direnci: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dayanim_seviyesi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fiyat: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    para_birimi: Mapped[str] = mapped_column(String(3), default="TRY")
    stok: Mapped[int] = mapped_column(Integer, default=0)
    aktif: Mapped[bool] = mapped_column(Boolean, default=True)
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_products_marka", "marka"),
        Index("idx_products_koleksiyon", "koleksiyon"),
        Index("idx_products_urun_tipi", "urun_tipi"),
        Index("idx_products_ana_renk", "ana_renk"),
        Index("idx_products_servis_tipi", "servis_tipi"),
        Index("idx_products_aktif", "aktif"),
        Index("idx_products_materyal", "materyal"),
    )
