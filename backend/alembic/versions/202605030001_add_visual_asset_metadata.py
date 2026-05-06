"""add visual asset metadata

Revision ID: 202605030001
Revises: 202605020001
Create Date: 2026-05-03 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605030001"
down_revision: Union[str, Sequence[str], None] = "202605020001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reference_images", sa.Column("asset_name", sa.String(), nullable=True))
    op.add_column("reference_images", sa.Column("asset_kind", sa.String(), nullable=True))
    op.add_column("reference_images", sa.Column("usage_note", sa.Text(), nullable=True))
    op.add_column("reference_images", sa.Column("asset_analysis", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("reference_images", "asset_analysis")
    op.drop_column("reference_images", "usage_note")
    op.drop_column("reference_images", "asset_kind")
    op.drop_column("reference_images", "asset_name")
