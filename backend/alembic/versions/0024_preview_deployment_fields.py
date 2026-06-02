"""add preview deployment / revert columns for workflow slice 4

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-02

Slice 4 of the workflow rollout: persist the metadata produced by the new
``deploy-preview`` and ``revert-preview`` Work Item actions. All columns are
nullable so existing rows are unaffected. Downgrade drops just the columns
added here.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    sa.Column("preview_status", sa.String(length=30), nullable=True),
    sa.Column("preview_url", sa.String(length=500), nullable=True),
    sa.Column("preview_branch", sa.String(length=200), nullable=True),
    sa.Column("preview_pr_number", sa.Integer(), nullable=True),
    sa.Column("preview_commit_sha", sa.String(length=40), nullable=True),
    sa.Column("preview_deployed_at", sa.DateTime(), nullable=True),
    sa.Column("preview_deployed_by", sa.String(length=100), nullable=True),
    sa.Column("preview_health_status", sa.String(length=30), nullable=True),
    sa.Column("preview_error", sa.Text(), nullable=True),
    sa.Column("preview_reverted_at", sa.DateTime(), nullable=True),
    sa.Column("preview_reverted_by", sa.String(length=100), nullable=True),
    sa.Column("preview_revert_reason", sa.Text(), nullable=True),
)


def upgrade() -> None:
    for column in _NEW_COLUMNS:
        op.add_column("work_items", column.copy())


def downgrade() -> None:
    for column in reversed(_NEW_COLUMNS):
        op.drop_column("work_items", column.name)
