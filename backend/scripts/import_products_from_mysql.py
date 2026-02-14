"""Import products from MySQL (uyumpro-idfine-db) into PostgreSQL."""

import asyncio
import sys

import pymysql

from sqlalchemy import text
from app.db.database import async_session, engine, Base
from app.models.product import Product


MYSQL_CONFIG = {
    "host": "host.docker.internal",  # Access host MySQL from Docker
    "user": "root",
    "password": "2025",
    "database": "uyumpro-idfine-db",
    "charset": "utf8mb4",
    "ssl_disabled": True,
}

# Columns to copy from MySQL to PostgreSQL
COLUMNS = [
    "urun_kodu", "urun_tanimi", "marka", "koleksiyon", "model", "urun_tipi",
    "ebat_cm", "hacim_cc", "materyal", "ana_renk", "renk_tonu", "form",
    "stil", "yuzey_bitisi", "dekor", "segment", "fiyat_segmenti",
    "kullanim_alani", "servis_tipi", "mutfak_uyumu", "yemek_onerileri",
    "konsept_etiketler", "rekabet_avantaji", "istiflenebilirlik",
    "kenar_citlama_dayanimi", "cizilme_direnci", "dayanim_seviyesi",
    "fiyat", "para_birimi", "stok", "aktif", "image",
]


def fetch_mysql_products() -> list[dict]:
    """Fetch all active products from MySQL."""
    conn = pymysql.connect(**MYSQL_CONFIG)
    try:
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cols = ", ".join(COLUMNS)
        cursor.execute(f"SELECT {cols} FROM products_idfine WHERE aktif = 1")
        rows = cursor.fetchall()
        print(f"Fetched {len(rows)} active products from MySQL")
        return rows
    finally:
        conn.close()


async def import_to_postgres(rows: list[dict]) -> None:
    """Import products into PostgreSQL."""
    # Ensure table exists
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        # Clear existing products
        await session.execute(text("DELETE FROM products"))
        await session.flush()

        # Insert in batches
        batch_size = 200
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            for row in batch:
                product = Product(**row)
                session.add(product)
            await session.flush()
            print(f"  Inserted {min(i + batch_size, len(rows))}/{len(rows)}")

        await session.commit()
    print(f"Import complete: {len(rows)} products in PostgreSQL")


async def main():
    print("=== MySQL → PostgreSQL Product Import ===")
    rows = fetch_mysql_products()
    if not rows:
        print("No products found!")
        return
    await import_to_postgres(rows)

    # Print summary
    async with async_session() as session:
        result = await session.execute(text("SELECT COUNT(*) FROM products WHERE aktif = true"))
        count = result.scalar()
        result2 = await session.execute(text("SELECT COUNT(*) FROM products WHERE aktif = true AND fiyat > 0"))
        with_price = result2.scalar()
        print(f"\nSummary: {count} active products, {with_price} with price > 0")


if __name__ == "__main__":
    asyncio.run(main())
