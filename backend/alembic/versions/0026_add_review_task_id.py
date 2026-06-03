"""add review_task_id to builder_tasks

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-02

Add review_task_id column to builder_tasks table to link builder tasks
to their corresponding Codex review tasks in Hermes Kanban.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "builder_tasks",
        sa.Column("review_task_id", sa.String(length=50), nullable=True),
    )
    op.create_index(
        "ix_builder_tasks_review_task_id",
        "builder_tasks",
        ["review_task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_builder_tasks_review_task_id", table_name="builder_tasks")
    op.drop_column("builder_tasks", "review_task_id")
