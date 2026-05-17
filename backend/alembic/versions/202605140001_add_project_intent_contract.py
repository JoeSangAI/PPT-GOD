"""add project intent contract

Revision ID: 202605140001
Revises: 202605060002
Create Date: 2026-05-14 13:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605140001"
down_revision: Union[str, None] = "202605060002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("intent_contract", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("projects", "intent_contract")
