from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ProductBase(BaseModel):
    urun_kodu: str = Field(min_length=1)
    urun_tanimi: str | None = None
    marka: str | None = None
    koleksiyon: str | None = None
    model: str | None = None
    urun_tipi: str | None = None
    ebat_cm: str | None = None
    hacim_cc: int | None = None
    materyal: str | None = None
    ana_renk: str | None = None
    renk_tonu: str | None = None
    form: str | None = None
    stil: str | None = None
    yuzey_bitisi: str | None = None
    dekor: str | None = None
    segment: str | None = None
    fiyat_segmenti: str | None = None
    kullanim_alani: str | None = None
    servis_tipi: str | None = None
    mutfak_uyumu: str | None = None
    menu_ana_baslik: str | None = None
    yemek_onerileri: str | None = None
    konsept_etiketler: str | None = None
    rekabet_avantaji: str | None = None
    istiflenebilirlik: str | None = None
    kenar_citlama_dayanimi: str | None = None
    cizilme_direnci: str | None = None
    dayanim_seviyesi: str | None = None
    fiyat: Decimal | None = None
    para_birimi: str = "TRY"
    stok: int = 0
    aktif: bool = True
    image: str | None = None


class CreateProductRequest(ProductBase):
    pass


class UpdateProductRequest(BaseModel):
    urun_kodu: str | None = None
    urun_tanimi: str | None = None
    marka: str | None = None
    koleksiyon: str | None = None
    model: str | None = None
    urun_tipi: str | None = None
    ebat_cm: str | None = None
    hacim_cc: int | None = None
    materyal: str | None = None
    ana_renk: str | None = None
    renk_tonu: str | None = None
    form: str | None = None
    stil: str | None = None
    yuzey_bitisi: str | None = None
    dekor: str | None = None
    segment: str | None = None
    fiyat_segmenti: str | None = None
    kullanim_alani: str | None = None
    servis_tipi: str | None = None
    mutfak_uyumu: str | None = None
    menu_ana_baslik: str | None = None
    yemek_onerileri: str | None = None
    konsept_etiketler: str | None = None
    rekabet_avantaji: str | None = None
    istiflenebilirlik: str | None = None
    kenar_citlama_dayanimi: str | None = None
    cizilme_direnci: str | None = None
    dayanim_seviyesi: str | None = None
    fiyat: Decimal | None = None
    para_birimi: str | None = None
    stok: int | None = None
    aktif: bool | None = None
    image: str | None = None


class ProductResponse(ProductBase):
    id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProductListResponse(BaseModel):
    products: list[ProductResponse]
    total: int
