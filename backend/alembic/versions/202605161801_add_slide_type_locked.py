"""add slide type_locked

Revision ID: 202605161801
Revises: 202605140001
Create Date: 2026-05-16 18:01:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605161801"
down_revision: Union[str, None] = "202605140001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("slides", sa.Column("type_locked", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("slides", "type_locked")
