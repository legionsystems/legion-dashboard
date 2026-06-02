"""add debate warmup and cleanup fields

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-31

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add warmup fields to debate_execution_config
    op.add_column("debate_execution_config", sa.Column("warm_model_before_debate", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("debate_execution_config", sa.Column("warmup_timeout_seconds", sa.Integer(), nullable=False, server_default="300"))
    op.add_column("debate_execution_config", sa.Column("keep_model_loaded_for", sa.String(length=20), nullable=False, server_default="'1h'"))
    op.add_column("debate_execution_config", sa.Column("fail_debate_if_warmup_fails", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("debate_execution_config", sa.Column("visible_failed_runs_limit", sa.Integer(), nullable=False, server_default="2"))

    # Add execution tracking fields to debate_runs
    op.add_column("debate_runs", sa.Column("execution_stage", sa.String(length=20), nullable=True))
    op.add_column("debate_runs", sa.Column("warmup_started_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("warmup_completed_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("warmup_duration_ms", sa.Integer(), nullable=True))
    op.add_column("debate_runs", sa.Column("warmup_method", sa.String(length=50), nullable=True))
    op.add_column("debate_runs", sa.Column("warmup_error", sa.Text(), nullable=True))
    op.add_column("debate_runs", sa.Column("generation_started_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("generation_completed_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("generation_duration_ms", sa.Integer(), nullable=True))
    op.add_column("debate_runs", sa.Column("error_type", sa.String(length=50), nullable=True))

    # Add cleanup/visibility fields to debate_runs
    op.add_column("debate_runs", sa.Column("hidden_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("hidden_by", sa.String(length=100), nullable=True))
    op.add_column("debate_runs", sa.Column("hidden_reason", sa.Text(), nullable=True))
    op.add_column("debate_runs", sa.Column("hidden_category", sa.String(length=50), nullable=True))
    op.add_column("debate_runs", sa.Column("is_test_run", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("debate_runs", sa.Column("superseded_by_run_id", sa.Integer(), nullable=True))
    op.add_column("debate_runs", sa.Column("cleanup_note", sa.Text(), nullable=True))


def downgrade() -> None:
    # Remove cleanup/visibility fields
    op.drop_column("debate_runs", "cleanup_note")
    op.drop_column("debate_runs", "superseded_by_run_id")
    op.drop_column("debate_runs", "is_test_run")
    op.drop_column("debate_runs", "hidden_category")
    op.drop_column("debate_runs", "hidden_reason")
    op.drop_column("debate_runs", "hidden_by")
    op.drop_column("debate_runs", "hidden_at")

    # Remove execution tracking fields
    op.drop_column("debate_runs", "error_type")
    op.drop_column("debate_runs", "generation_duration_ms")
    op.drop_column("debate_runs", "generation_completed_at")
    op.drop_column("debate_runs", "generation_started_at")
    op.drop_column("debate_runs", "warmup_error")
    op.drop_column("debate_runs", "warmup_method")
    op.drop_column("debate_runs", "warmup_duration_ms")
    op.drop_column("debate_runs", "warmup_completed_at")
    op.drop_column("debate_runs", "warmup_started_at")
    op.drop_column("debate_runs", "execution_stage")

    # Remove warmup fields from config
    op.drop_column("debate_execution_config", "visible_failed_runs_limit")
    op.drop_column("debate_execution_config", "fail_debate_if_warmup_fails")
    op.drop_column("debate_execution_config", "keep_model_loaded_for")
    op.drop_column("debate_execution_config", "warmup_timeout_seconds")
    op.drop_column("debate_execution_config", "warm_model_before_debate")
