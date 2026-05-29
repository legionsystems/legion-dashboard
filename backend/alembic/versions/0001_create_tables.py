"""create work_items and follow_ups tables

Revision ID: 0001
Revises:
Create Date: 2026-05-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(length=30), nullable=False, server_default="task"),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="draft"),
        sa.Column(
            "approved_by_operator",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("approval_timestamp", sa.DateTime(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("override_timestamp", sa.DateTime(), nullable=True),
        sa.Column("builder_profile", sa.String(length=100), nullable=True),
        sa.Column("builder_model", sa.String(length=200), nullable=True),
        sa.Column("builder_provider", sa.String(length=100), nullable=True),
        sa.Column("reviewer_profile", sa.String(length=100), nullable=True),
        sa.Column("reviewer_model", sa.String(length=200), nullable=True),
        sa.Column("reviewer_provider", sa.String(length=100), nullable=True),
        sa.Column(
            "same_model_blocked",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("pr_url", sa.String(length=500), nullable=True),
        sa.Column("merge_commit_sha", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_work_items_id", "work_items", ["id"])

    op.create_table(
        "follow_ups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "work_item_id",
            sa.Integer(),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.String(length=20),
            nullable=False,
            server_default="non-blocking",
        ),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="open"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_follow_ups_id", "follow_ups", ["id"])
    op.create_index(
        "ix_follow_ups_work_item_id", "follow_ups", ["work_item_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_follow_ups_work_item_id", table_name="follow_ups")
    op.drop_index("ix_follow_ups_id", table_name="follow_ups")
    op.drop_table("follow_ups")
    op.drop_index("ix_work_items_id", table_name="work_items")
    op.drop_table("work_items")
