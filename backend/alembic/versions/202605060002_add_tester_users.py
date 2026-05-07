"""add tester users

Revision ID: 202605060002
Revises: 202605060001
Create Date: 2026-05-06 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "202605060002"
down_revision: Union[str, None] = "202605060001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tester_users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("login_key", sa.String(), nullable=False),
        sa.Column("passcode_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tester_users_login_key"), "tester_users", ["login_key"], unique=True)
    op.add_column("projects", sa.Column("tester_id", sa.String(), nullable=True))
    op.create_index(op.f("ix_projects_tester_id"), "projects", ["tester_id"], unique=False)
    op.create_foreign_key(None, "projects", "tester_users", ["tester_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint(None, "projects", type_="foreignkey")
    op.drop_index(op.f("ix_projects_tester_id"), table_name="projects")
    op.drop_column("projects", "tester_id")
    op.drop_index(op.f("ix_tester_users_login_key"), table_name="tester_users")
    op.drop_table("tester_users")

