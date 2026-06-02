"""add merge / complete columns for workflow slice 5

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-02

Slice 5 of the workflow rollout: persist the metadata produced by the new
``merge`` and ``complete`` Work Item actions. All columns are nullable so
existing rows are unaffected. Downgrade drops just the columns added here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025"
down_revision: Union[str, None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    sa.Column("merge_status", sa.String(length=30), nullable=True),
    sa.Column("merged_at", sa.DateTime(), nullable=True),
    sa.Column("merged_by", sa.String(length=100), nullable=True),
    sa.Column("merge_note", sa.Text(), nullable=True),
    sa.Column("merge_error", sa.Text(), nullable=True),
    sa.Column("post_merge_verified_at", sa.DateTime(), nullable=True),
    sa.Column("post_merge_verified_by", sa.String(length=100), nullable=True),
    sa.Column("post_merge_health_status", sa.String(length=30), nullable=True),
    sa.Column("completed_at", sa.DateTime(), nullable=True),
    sa.Column("completed_by", sa.String(length=100), nullable=True),
    sa.Column("completion_note", sa.Text(), nullable=True),
)


def upgrade() -> None:
    for column in _NEW_COLUMNS:
        op.add_column("work_items", column.copy())


def downgrade() -> None:
    for column in reversed(_NEW_COLUMNS):
        op.drop_column("work_items", column.name)
