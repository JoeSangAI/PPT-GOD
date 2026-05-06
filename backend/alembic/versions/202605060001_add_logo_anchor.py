"""add logo anchor

Revision ID: 202605060001
Revises: 202605030001
Create Date: 2026-05-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605060001"
down_revision: Union[str, Sequence[str], None] = "202605030001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reference_images", sa.Column("logo_anchor", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("reference_images", "logo_anchor")
