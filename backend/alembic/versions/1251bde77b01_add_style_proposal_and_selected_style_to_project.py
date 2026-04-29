"""add style_proposal and selected_style to project

Revision ID: 1251bde77b01
Revises: 4c3107fea191
Create Date: 2026-04-25 18:11:43.148173

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = '1251bde77b01'
down_revision: Union[str, Sequence[str], None] = '4c3107fea191'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('style_proposal', sqlite.JSON(), nullable=True))
    op.add_column('projects', sa.Column('selected_style', sqlite.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'selected_style')
    op.drop_column('projects', 'style_proposal')
