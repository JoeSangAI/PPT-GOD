"""add selected_template_recommendations to project

Revision ID: 20260425190620
Revises: 1251bde77b01
Create Date: 2026-04-25 19:06:20.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = '20260425190620'
down_revision: Union[str, Sequence[str], None] = '1251bde77b01'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('projects', sa.Column('selected_template_recommendations', sqlite.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('projects', 'selected_template_recommendations')
