"""Add Odoo sync tracking fields to products table

Revision ID: 002_product_sync
Revises: 001_live_support
Create Date: 2026-02-17

New columns:
- products: odoo_product_id, odoo_write_date, last_synced_at
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "002_product_sync"
down_revision: Union[str, None] = "001_live_support"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("odoo_product_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("odoo_write_date", sa.String(30), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_products_odoo_product_id",
        "products",
        ["odoo_product_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_products_odoo_product_id", table_name="products")
    op.drop_column("products", "last_synced_at")
    op.drop_column("products", "odoo_write_date")
    op.drop_column("products", "odoo_product_id")
