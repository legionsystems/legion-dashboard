"""add debate worker fields

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-31

"""
from alembic import op
import sqlalchemy as sa


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade():
    # Add worker/queue fields to debate_runs
    op.add_column("debate_runs", sa.Column("worker_status", sa.String(length=20), nullable=False, server_default="queued"))
    op.add_column("debate_runs", sa.Column("worker_id", sa.String(length=100), nullable=True))
    op.add_column("debate_runs", sa.Column("lease_until", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("claimed_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("heartbeat_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("queued_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False))
    op.add_column("debate_runs", sa.Column("started_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("cancelled_by", sa.String(length=100), nullable=True))
    op.add_column("debate_runs", sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("debate_runs", sa.Column("retry_of_run_id", sa.Integer(), nullable=True))
    op.add_column("debate_runs", sa.Column("retry_from_turn_id", sa.Integer(), nullable=True))
    op.add_column("debate_runs", sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("debate_runs", sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"))
    
    # Add worker configuration to debate_execution_config
    op.add_column("debate_execution_config", sa.Column("execution_backend", sa.String(length=20), nullable=False, server_default="worker"))
    op.add_column("debate_execution_config", sa.Column("worker_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("debate_execution_config", sa.Column("worker_poll_interval_seconds", sa.Integer(), nullable=False, server_default="3"))
    op.add_column("debate_execution_config", sa.Column("worker_lease_seconds", sa.Integer(), nullable=False, server_default="300"))
    op.add_column("debate_execution_config", sa.Column("worker_heartbeat_seconds", sa.Integer(), nullable=False, server_default="10"))
    op.add_column("debate_execution_config", sa.Column("worker_max_concurrent_runs", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("debate_execution_config", sa.Column("turn_timeout_seconds", sa.Integer(), nullable=True))
    op.add_column("debate_execution_config", sa.Column("whole_run_timeout_seconds", sa.Integer(), nullable=True))
    op.add_column("debate_execution_config", sa.Column("retry_failed_turn_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("debate_execution_config", sa.Column("max_turn_retries", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    # Remove worker configuration from config
    op.drop_column("debate_execution_config", "max_turn_retries")
    op.drop_column("debate_execution_config", "retry_failed_turn_enabled")
    op.drop_column("debate_execution_config", "whole_run_timeout_seconds")
    op.drop_column("debate_execution_config", "turn_timeout_seconds")
    op.drop_column("debate_execution_config", "worker_max_concurrent_runs")
    op.drop_column("debate_execution_config", "worker_heartbeat_seconds")
    op.drop_column("debate_execution_config", "worker_lease_seconds")
    op.drop_column("debate_execution_config", "worker_poll_interval_seconds")
    op.drop_column("debate_execution_config", "worker_enabled")
    op.drop_column("debate_execution_config", "execution_backend")
    
    # Remove worker/queue fields from debate_runs
    op.drop_column("debate_runs", "max_attempts")
    op.drop_column("debate_runs", "attempt_number")
    op.drop_column("debate_runs", "retry_from_turn_id")
    op.drop_column("debate_runs", "retry_of_run_id")
    op.drop_column("debate_runs", "cancel_requested")
    op.drop_column("debate_runs", "cancelled_by")
    op.drop_column("debate_runs", "cancelled_at")
    op.drop_column("debate_runs", "started_at")
    op.drop_column("debate_runs", "queued_at")
    op.drop_column("debate_runs", "heartbeat_at")
    op.drop_column("debate_runs", "claimed_at")
    op.drop_column("debate_runs", "lease_until")
    op.drop_column("debate_runs", "worker_id")
    op.drop_column("debate_runs", "worker_status")
