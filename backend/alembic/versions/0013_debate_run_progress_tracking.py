"""add debate run progress tracking fields

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-31

"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade():
    # Add progress tracking fields to debate_runs
    op.add_column("debate_runs", sa.Column("current_round", sa.Integer(), nullable=True))
    op.add_column("debate_runs", sa.Column("current_turn", sa.String(length=50), nullable=True))
    op.add_column("debate_runs", sa.Column("current_side", sa.String(length=10), nullable=True))
    op.add_column("debate_runs", sa.Column("current_role", sa.String(length=100), nullable=True))
    op.add_column("debate_runs", sa.Column("current_model", sa.String(length=200), nullable=True))
    op.add_column("debate_runs", sa.Column("last_progress_at", sa.DateTime(), nullable=True))
    op.add_column("debate_runs", sa.Column("progress_message", sa.Text(), nullable=True))
    
    # Add error detail fields
    op.add_column("debate_runs", sa.Column("error_stage", sa.String(length=20), nullable=True))
    op.add_column("debate_runs", sa.Column("error_round", sa.Integer(), nullable=True))
    op.add_column("debate_runs", sa.Column("error_turn", sa.String(length=50), nullable=True))
    op.add_column("debate_runs", sa.Column("error_elapsed_ms", sa.Integer(), nullable=True))
    
    # Update execution_stage comment to include 'generating'
    # (SQLite doesn't support ALTER COLUMN, but the enum is just a comment)


def downgrade():
    op.drop_column("debate_runs", "error_elapsed_ms")
    op.drop_column("debate_runs", "error_turn")
    op.drop_column("debate_runs", "error_round")
    op.drop_column("debate_runs", "error_stage")
    op.drop_column("debate_runs", "progress_message")
    op.drop_column("debate_runs", "last_progress_at")
    op.drop_column("debate_runs", "current_model")
    op.drop_column("debate_runs", "current_role")
    op.drop_column("debate_runs", "current_side")
    op.drop_column("debate_runs", "current_turn")
    op.drop_column("debate_runs", "current_round")
