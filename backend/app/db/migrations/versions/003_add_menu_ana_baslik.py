"""Add menu_ana_baslik column to products table

Revision ID: 003_menu_ana_baslik
Revises: 002_product_sync
Create Date: 2026-02-19

New columns:
- products: menu_ana_baslik
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "003_menu_ana_baslik"
down_revision: Union[str, None] = "002_product_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("menu_ana_baslik", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("products", "menu_ana_baslik")
