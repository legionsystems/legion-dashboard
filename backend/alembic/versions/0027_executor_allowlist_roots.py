"""add executor allowlist roots + apply log tables

Revision ID: 0027
Revises: 0026_debate_archive_and_reset
Create Date: 2026-06-04

Surfaces the executor's allowed-repo-roots config in the LEGION Dashboard.
The dashboard owns the persisted list; an apply endpoint writes the enabled
rows back to ``/etc/legion/executor-allowlist.conf`` and restarts the host
``legion-preview-executor`` unit.

The migration seeds the three baked-in defaults from
``ops/host-executor/legion-preview-executor::_DEFAULT_ALLOWED_REPO_ROOTS``
so a fresh install boots with the same effective allowlist the executor
ships with. Defaults are flagged ``is_default = True`` — the API refuses
to delete them; they can be disabled but not removed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0027"
down_revision: Union[str, None] = "0026_debate_archive_and_reset"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SEED_DEFAULT_ROOTS = (
    "/srv/repo/legion-dashboard",
    "/srv/repo/lgn-hub",
    "/srv/worktrees/legion-dashboard",
)


def upgrade() -> None:
    op.create_table(
        "executor_allowlist_roots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("path", sa.String(length=500), nullable=False, unique=True),
        sa.Column(
            "added_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("added_by", sa.String(length=200), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        op.f("ix_executor_allowlist_roots_id"),
        "executor_allowlist_roots",
        ["id"],
        unique=False,
    )

    op.create_table(
        "executor_allowlist_apply_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "applied_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("applied_by", sa.String(length=200), nullable=True),
        sa.Column("joined_roots", sa.Text(), nullable=False),
        sa.Column("config_path", sa.String(length=500), nullable=False),
        sa.Column(
            "restart_ok",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index(
        op.f("ix_executor_allowlist_apply_log_id"),
        "executor_allowlist_apply_log",
        ["id"],
        unique=False,
    )

    # Seed the three baked-in defaults so the dashboard's effective list
    # matches the executor's source-defaults on a fresh install.
    bind = op.get_bind()
    seed_table = sa.table(
        "executor_allowlist_roots",
        sa.column("path", sa.String),
        sa.column("added_by", sa.String),
        sa.column("note", sa.String),
        sa.column("is_default", sa.Boolean),
        sa.column("is_enabled", sa.Boolean),
    )
    bind.execute(
        seed_table.insert(),
        [
            {
                "path": path,
                "added_by": "system",
                "note": "Seeded default root (matches executor source).",
                "is_default": True,
                "is_enabled": True,
            }
            for path in _SEED_DEFAULT_ROOTS
        ],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_executor_allowlist_apply_log_id"),
        table_name="executor_allowlist_apply_log",
    )
    op.drop_table("executor_allowlist_apply_log")
    op.drop_index(
        op.f("ix_executor_allowlist_roots_id"),
        table_name="executor_allowlist_roots",
    )
    op.drop_table("executor_allowlist_roots")
