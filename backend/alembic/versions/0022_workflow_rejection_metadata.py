"""add operator rejection metadata to work_items

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-02

Slice 2 of the workflow rollout: introduce metadata columns that capture
operator rejection ("not acceptable, abandon") and change-request ("needs
rework, send back") actions. All columns are nullable so existing rows are
unaffected and no backfill is required.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    ("rejected_at", sa.DateTime()),
    ("rejected_by", sa.String(length=100)),
    ("rejection_reason", sa.Text()),
    ("changes_requested_at", sa.DateTime()),
    ("changes_requested_by", sa.String(length=100)),
    ("change_request", sa.Text()),
)


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        for name, col_type in _NEW_COLUMNS:
            batch.add_column(sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        for name, _ in reversed(_NEW_COLUMNS):
            batch.drop_column(name)
