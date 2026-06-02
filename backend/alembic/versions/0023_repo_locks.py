"""add repo_locks table for workflow slice 3

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-02

Slice 3 of the workflow rollout: introduce a ``repo_locks`` table that records
the lock held by an in-flight builder run on a target repo. The lock is the
canonical authority for "another build is already touching this repo".

The table is new; no existing data is migrated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023"
down_revision: Union[str, None] = "0022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "repo_locks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("repo_path", sa.String(length=500), nullable=False, unique=True),
        sa.Column("repo_name", sa.String(length=100), nullable=False),
        sa.Column("work_item_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.String(length=100), nullable=True),
        sa.Column("branch_name", sa.String(length=100), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("lock_owner", sa.String(length=100), nullable=True),
        sa.Column(
            "lock_status",
            sa.String(length=20),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("release_reason", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("repo_locks")
