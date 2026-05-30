"""add debate_execution_config table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "debate_execution_config",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Always 1 for singleton pattern

        # Core settings
        sa.Column("enabled", sa.Boolean(), nullable=False, default=False),
        sa.Column("provider", sa.String(length=50), nullable=False, default="openai_compatible"),
        sa.Column("base_url", sa.String(length=500), nullable=False, default="http://ai-4080:11434/v1"),

        # Model mode: single_model (default) or role_models (advanced)
        sa.Column("model_mode", sa.String(length=20), nullable=False, default="single_model"),

        # Single-model mode: all roles use this model
        sa.Column("default_model", sa.String(length=200), nullable=False, default="deepseek-r1:32b"),

        # Role-specific models (only used when model_mode='role_models')
        sa.Column("pro_model", sa.String(length=200), nullable=True),
        sa.Column("con_model", sa.String(length=200), nullable=True),
        sa.Column("arbiter_model", sa.String(length=200), nullable=True),
        sa.Column("fallback_model", sa.String(length=200), nullable=True),

        # Secret — stored server-side only
        sa.Column("api_key", sa.String(length=500), nullable=True),

        # Timeouts and limits
        sa.Column("timeout_seconds", sa.Integer(), nullable=False, default=180),
        sa.Column("max_output_chars", sa.Integer(), nullable=False, default=12000),

        # Debate defaults
        sa.Column("default_rounds", sa.Integer(), nullable=False, default=2),

        # Security guards
        sa.Column("allow_cloud_endpoints", sa.Boolean(), nullable=False, default=False),

        # Metadata
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_debate_execution_config_id", "debate_execution_config", ["id"])

    # Insert default singleton row
    op.execute(
        """
        INSERT INTO debate_execution_config (
            id, enabled, provider, base_url, model_mode, default_model,
            pro_model, con_model, arbiter_model, fallback_model,
            api_key, timeout_seconds, max_output_chars, default_rounds,
            allow_cloud_endpoints, notes
        ) VALUES (
            1, false, 'openai_compatible', 'http://ai-4080:11434/v1', 'single_model', 'deepseek-r1:32b',
            NULL, NULL, NULL, NULL,
            NULL, 180, 12000, 2, false, NULL
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_debate_execution_config_id", table_name="debate_execution_config")
    op.drop_table("debate_execution_config")
