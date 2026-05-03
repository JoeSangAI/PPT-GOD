"""add project runs

Revision ID: 202605020001
Revises: 20260430120000
Create Date: 2026-05-02 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite


revision: str = "202605020001"
down_revision: Union[str, Sequence[str], None] = "20260430120000"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("target_page_nums", sqlite.JSON(), nullable=True),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("error_msg", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_runs_project_status", "project_runs", ["project_id", "status"])
    op.create_index("ix_project_runs_project_started", "project_runs", ["project_id", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_project_runs_project_started", table_name="project_runs")
    op.drop_index("ix_project_runs_project_status", table_name="project_runs")
    op.drop_table("project_runs")
