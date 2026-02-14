from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.dependencies import require_permission
from app.models.product import Product
from app.models.user import User
from app.schemas.product import (
    CreateProductRequest,
    ProductListResponse,
    ProductResponse,
    UpdateProductRequest,
)

router = APIRouter(prefix="/admin/products", tags=["products"])


@router.get("", response_model=ProductListResponse)
async def list_products(
    user: Annotated[User, Depends(require_permission("documents.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    search: str | None = Query(None),
    marka: str | None = Query(None),
    koleksiyon: str | None = Query(None),
    urun_tipi: str | None = Query(None),
    aktif: bool | None = Query(None),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
):
    """List products with pagination, search, and filters."""
    query = select(Product)
    count_query = select(func.count(Product.id))

    if search:
        search_filter = or_(
            Product.urun_kodu.ilike(f"%{search}%"),
            Product.urun_tanimi.ilike(f"%{search}%"),
            Product.marka.ilike(f"%{search}%"),
            Product.koleksiyon.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)
    if marka:
        query = query.where(Product.marka == marka)
        count_query = count_query.where(Product.marka == marka)
    if koleksiyon:
        query = query.where(Product.koleksiyon == koleksiyon)
        count_query = count_query.where(Product.koleksiyon == koleksiyon)
    if urun_tipi:
        query = query.where(Product.urun_tipi == urun_tipi)
        count_query = count_query.where(Product.urun_tipi == urun_tipi)
    if aktif is not None:
        query = query.where(Product.aktif == aktif)
        count_query = count_query.where(Product.aktif == aktif)

    total = (await db.execute(count_query)).scalar() or 0
    result = await db.execute(
        query.order_by(Product.id.desc()).limit(limit).offset(offset)
    )
    products = result.scalars().all()

    return ProductListResponse(
        products=[ProductResponse.model_validate(p) for p in products],
        total=total,
    )


@router.get("/filters")
async def get_product_filters(
    user: Annotated[User, Depends(require_permission("documents.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get distinct values for filter dropdowns."""
    markalar = (await db.execute(
        select(Product.marka).where(Product.marka.isnot(None)).distinct().order_by(Product.marka)
    )).scalars().all()
    koleksiyonlar = (await db.execute(
        select(Product.koleksiyon).where(Product.koleksiyon.isnot(None)).distinct().order_by(Product.koleksiyon)
    )).scalars().all()
    urun_tipleri = (await db.execute(
        select(Product.urun_tipi).where(Product.urun_tipi.isnot(None)).distinct().order_by(Product.urun_tipi)
    )).scalars().all()
    return {
        "markalar": markalar,
        "koleksiyonlar": koleksiyonlar,
        "urun_tipleri": urun_tipleri,
    }


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int,
    user: Annotated[User, Depends(require_permission("documents.view"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get single product detail."""
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Ürün bulunamadı")
    return ProductResponse.model_validate(product)


@router.post("", response_model=ProductResponse)
async def create_product(
    body: CreateProductRequest,
    user: Annotated[User, Depends(require_permission("documents.upload"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Create a new product."""
    # Check duplicate urun_kodu
    existing = await db.execute(
        select(Product).where(Product.urun_kodu == body.urun_kodu)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Bu ürün kodu zaten mevcut")

    product = Product(**body.model_dump())
    db.add(product)
    await db.flush()
    await db.commit()
    await db.refresh(product)

    return ProductResponse.model_validate(product)


@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    body: UpdateProductRequest,
    user: Annotated[User, Depends(require_permission("documents.upload"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a product."""
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Ürün bulunamadı")

    # Check duplicate urun_kodu if changing
    if body.urun_kodu is not None and body.urun_kodu != product.urun_kodu:
        existing = await db.execute(
            select(Product).where(
                Product.urun_kodu == body.urun_kodu, Product.id != product_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(409, "Bu ürün kodu zaten mevcut")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(product, key, value)

    await db.flush()
    await db.commit()
    await db.refresh(product)

    return ProductResponse.model_validate(product)


@router.delete("/{product_id}")
async def delete_product(
    product_id: int,
    user: Annotated[User, Depends(require_permission("documents.delete"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a product."""
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(404, "Ürün bulunamadı")

    await db.delete(product)
    await db.commit()

    return {"status": "ok", "message": "Ürün silindi"}
