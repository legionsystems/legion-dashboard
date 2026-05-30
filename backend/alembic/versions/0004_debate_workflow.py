"""add debate_runs, debate_arguments, operator_debate_inputs

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "debate_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "work_item_id",
            sa.Integer(),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_item_type_snapshot", sa.String(length=30), nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="queued",
        ),
        sa.Column(
            "rounds_requested",
            sa.Integer(),
            nullable=False,
            server_default="2",
        ),
        sa.Column(
            "rounds_completed",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "trigger",
            sa.String(length=30),
            nullable=False,
            server_default="automatic",
        ),
        sa.Column("model_route", sa.String(length=200), nullable=True),
        sa.Column("provenance", sa.Text(), nullable=True),
        sa.Column("input_snapshot_json", sa.Text(), nullable=True),
        sa.Column("changed_since_previous_json", sa.Text(), nullable=True),
        sa.Column(
            "final_recommendation", sa.String(length=40), nullable=True
        ),
        sa.Column(
            "implementation_readiness", sa.String(length=30), nullable=True
        ),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("risks", sa.Text(), nullable=True),
        sa.Column("suggested_title", sa.String(length=300), nullable=True),
        sa.Column("suggested_description", sa.Text(), nullable=True),
        sa.Column("suggested_acceptance_notes", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_debate_runs_id", "debate_runs", ["id"])
    op.create_index(
        "ix_debate_runs_work_item_id", "debate_runs", ["work_item_id"]
    )

    op.create_table(
        "debate_arguments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "debate_run_id",
            sa.Integer(),
            sa.ForeignKey("debate_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "round_number",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("role", sa.String(length=60), nullable=False),
        sa.Column(
            "side",
            sa.String(length=20),
            nullable=False,
            server_default="neutral",
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_debate_arguments_id", "debate_arguments", ["id"])
    op.create_index(
        "ix_debate_arguments_debate_run_id",
        "debate_arguments",
        ["debate_run_id"],
    )

    op.create_table(
        "operator_debate_inputs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "work_item_id",
            sa.Integer(),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "stance_requested",
            sa.String(length=20),
            nullable=False,
            server_default="auto_assign",
        ),
        sa.Column("stance_assigned", sa.String(length=20), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "considered_in_run_id",
            sa.Integer(),
            sa.ForeignKey("debate_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_operator_debate_inputs_id", "operator_debate_inputs", ["id"]
    )
    op.create_index(
        "ix_operator_debate_inputs_work_item_id",
        "operator_debate_inputs",
        ["work_item_id"],
    )
    op.create_index(
        "ix_operator_debate_inputs_considered_in_run_id",
        "operator_debate_inputs",
        ["considered_in_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operator_debate_inputs_considered_in_run_id",
        table_name="operator_debate_inputs",
    )
    op.drop_index(
        "ix_operator_debate_inputs_work_item_id",
        table_name="operator_debate_inputs",
    )
    op.drop_index(
        "ix_operator_debate_inputs_id", table_name="operator_debate_inputs"
    )
    op.drop_table("operator_debate_inputs")

    op.drop_index(
        "ix_debate_arguments_debate_run_id", table_name="debate_arguments"
    )
    op.drop_index("ix_debate_arguments_id", table_name="debate_arguments")
    op.drop_table("debate_arguments")

    op.drop_index("ix_debate_runs_work_item_id", table_name="debate_runs")
    op.drop_index("ix_debate_runs_id", table_name="debate_runs")
    op.drop_table("debate_runs")
