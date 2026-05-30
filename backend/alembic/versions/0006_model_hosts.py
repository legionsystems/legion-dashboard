"""add model_hosts and model_host_models tables

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create model_hosts table
    op.create_table(
        'model_hosts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False, server_default='openai_compatible'),
        sa.Column('base_url', sa.String(length=500), nullable=False),
        sa.Column('api_key', sa.String(length=500), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('allow_cloud_endpoints', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('last_test_status', sa.String(length=20), nullable=True),
        sa.Column('last_test_message', sa.Text(), nullable=True),
        sa.Column('last_tested_at', sa.DateTime(), nullable=True),
        sa.Column('last_models_refresh_at', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_model_hosts_id'), 'model_hosts', ['id'], unique=False)
    op.create_index(op.f('ix_model_hosts_name'), 'model_hosts', ['name'], unique=True)

    # Create model_host_models table
    op.create_table(
        'model_host_models',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('host_id', sa.Integer(), nullable=False),
        sa.Column('model_id', sa.String(length=200), nullable=False),
        sa.Column('display_name', sa.String(length=200), nullable=True),
        sa.Column('raw_json', sa.Text(), nullable=True),
        sa.Column('is_available', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('discovered_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.ForeignKeyConstraint(['host_id'], ['model_hosts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_model_host_models_id'), 'model_host_models', ['id'], unique=False)
    op.create_index(op.f('ix_model_host_models_host_id'), 'model_host_models', ['host_id'], unique=False)
    op.create_index(op.f('ix_model_host_models_model_id'), 'model_host_models', ['model_id'], unique=False)

    # Seed default ai-4080 host
    op.execute(
        sa.text("""
        INSERT INTO model_hosts (name, provider, base_url, enabled, allow_cloud_endpoints)
        VALUES ('ai-4080', 'openai_compatible', 'http://ai-4080:11434/v1', true, false)
        """)
    )


def downgrade() -> None:
    op.drop_table('model_host_models')
    op.drop_table('model_hosts')
