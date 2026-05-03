"""add missing fields to projects and reference_images

Revision ID: 20260430120000
Revises: 20260425190620
Create Date: 2026-04-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = '20260430120000'
down_revision: Union[str, Sequence[str], None] = '20260425190620'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add content_plan_confirmed to projects
    op.add_column('projects', sa.Column('content_plan_confirmed', sa.Boolean(), nullable=False, server_default='0'))
    # Add slide_id and process_mode to reference_images
    op.add_column('reference_images', sa.Column('slide_id', sa.VARCHAR(), nullable=True))
    op.add_column('reference_images', sa.Column('process_mode', sa.VARCHAR(), nullable=True))
    # Add foreign key for reference_images.slide_id -> slides.id
    op.create_foreign_key('fk_reference_images_slide_id', 'reference_images', 'slides', ['slide_id'], ['id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_reference_images_slide_id', 'reference_images', type_='foreignkey')
    op.drop_column('reference_images', 'process_mode')
    op.drop_column('reference_images', 'slide_id')
    op.drop_column('projects', 'content_plan_confirmed')
