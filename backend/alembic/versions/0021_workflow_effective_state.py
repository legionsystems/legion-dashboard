"""add workflow lifecycle/effective-state metadata to work_items

Revision ID: 0021
Revises: 238aa144c662
Create Date: 2026-06-02

Slice 1 of the workflow rollout: introduce metadata columns that downstream
slices (operator certification, repo lock, preview deploy, merge automation)
will populate. All columns are nullable so existing rows are unaffected and no
backfill is required.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0021"
down_revision: Union[str, None] = "238aa144c662"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    ("dashboard_lifecycle_status", sa.String(length=40)),
    ("effective_state", sa.String(length=40)),
    ("pr_number", sa.Integer()),
    ("code_review_status", sa.String(length=30)),
    ("branch_name", sa.String(length=200)),
    ("preview_required", sa.Boolean()),
    ("preview_deployed", sa.Boolean()),
    ("operator_certified", sa.Boolean()),
    ("ready_to_merge", sa.Boolean()),
    ("certified_at", sa.DateTime()),
    ("certified_by", sa.String(length=100)),
    ("certification_note", sa.Text()),
)


def upgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        for name, col_type in _NEW_COLUMNS:
            batch.add_column(sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("work_items") as batch:
        for name, _ in reversed(_NEW_COLUMNS):
            batch.drop_column(name)
