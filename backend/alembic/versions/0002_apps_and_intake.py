"""add apps + app_action_logs and work_item intake fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column("target_app", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "work_items",
        sa.Column(
            "priority",
            sa.String(length=20),
            nullable=False,
            server_default="medium",
        ),
    )
    op.add_column(
        "work_items",
        sa.Column("tags", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "work_items",
        sa.Column(
            "source",
            sa.String(length=30),
            nullable=False,
            server_default="operator",
        ),
    )
    op.add_column(
        "work_items",
        sa.Column("acceptance_notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "apps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("repo", sa.String(length=500), nullable=True),
        sa.Column("compose_project", sa.String(length=100), nullable=False),
        sa.Column("compose_path", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("last_action", sa.String(length=20), nullable=True),
        sa.Column("last_result", sa.String(length=20), nullable=True),
        sa.Column("last_updated", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("app_id", name="uq_apps_app_id"),
    )
    op.create_index("ix_apps_id", "apps", ["id"])
    op.create_index("ix_apps_app_id", "apps", ["app_id"])

    op.create_table(
        "app_action_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "app_id",
            sa.Integer(),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("result", sa.String(length=20), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout_tail", sa.Text(), nullable=True),
        sa.Column("stderr_tail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_app_action_logs_id", "app_action_logs", ["id"])
    op.create_index(
        "ix_app_action_logs_app_id", "app_action_logs", ["app_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_app_action_logs_app_id", table_name="app_action_logs")
    op.drop_index("ix_app_action_logs_id", table_name="app_action_logs")
    op.drop_table("app_action_logs")
    op.drop_index("ix_apps_app_id", table_name="apps")
    op.drop_index("ix_apps_id", table_name="apps")
    op.drop_table("apps")

    op.drop_column("work_items", "acceptance_notes")
    op.drop_column("work_items", "source")
    op.drop_column("work_items", "tags")
    op.drop_column("work_items", "priority")
    op.drop_column("work_items", "target_app")
