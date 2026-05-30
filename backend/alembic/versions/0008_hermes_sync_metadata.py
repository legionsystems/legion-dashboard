"""add Hermes sync metadata to model_hosts

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add Hermes sync metadata columns to model_hosts
    # Use column existence checks for safety in case of partial migrations
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    model_hosts_columns = [c['name'] for c in inspector.get_columns('model_hosts')]
    
    if 'source' not in model_hosts_columns:
        op.add_column('model_hosts', sa.Column('source', sa.String(length=20), nullable=False, server_default='manual'))
    if 'source_key' not in model_hosts_columns:
        op.add_column('model_hosts', sa.Column('source_key', sa.String(length=200), nullable=True))
    if 'profile_name' not in model_hosts_columns:
        op.add_column('model_hosts', sa.Column('profile_name', sa.String(length=100), nullable=True))
    if 'provider_name' not in model_hosts_columns:
        op.add_column('model_hosts', sa.Column('provider_name', sa.String(length=100), nullable=True))
    if 'sync_enabled' not in model_hosts_columns:
        op.add_column('model_hosts', sa.Column('sync_enabled', sa.Boolean(), nullable=False, server_default='true'))
    if 'last_synced_at' not in model_hosts_columns:
        op.add_column('model_hosts', sa.Column('last_synced_at', sa.DateTime(), nullable=True))
    if 'last_sync_status' not in model_hosts_columns:
        op.add_column('model_hosts', sa.Column('last_sync_status', sa.String(length=20), nullable=True))
    if 'last_sync_error' not in model_hosts_columns:
        op.add_column('model_hosts', sa.Column('last_sync_error', sa.Text(), nullable=True))
    
    # Add sync metadata to model_host_models
    model_host_models_columns = [c['name'] for c in inspector.get_columns('model_host_models')]
    
    if 'source' not in model_host_models_columns:
        op.add_column('model_host_models', sa.Column('source', sa.String(length=20), nullable=False, server_default='manual'))
    if 'source_key' not in model_host_models_columns:
        op.add_column('model_host_models', sa.Column('source_key', sa.String(length=200), nullable=True))
    if 'last_synced_at' not in model_host_models_columns:
        op.add_column('model_host_models', sa.Column('last_synced_at', sa.DateTime(), nullable=True))
    
    # Create model_sync_runs table for sync history (if not exists)
    if not inspector.has_table('model_sync_runs'):
        op.create_table(
            'model_sync_runs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('source', sa.String(length=20), nullable=False, default='hermes'),
            sa.Column('status', sa.String(length=20), nullable=False, default='running'),
            sa.Column('hosts_discovered', sa.Integer(), nullable=True),
            sa.Column('models_discovered', sa.Integer(), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_model_sync_runs_id'), 'model_sync_runs', ['id'], unique=False)
        op.create_index(op.f('ix_model_sync_runs_created_at'), 'model_sync_runs', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_table('model_sync_runs')
    op.drop_column('model_host_models', 'last_synced_at')
    op.drop_column('model_host_models', 'source_key')
    op.drop_column('model_host_models', 'source')
    op.drop_column('model_hosts', 'last_sync_error')
    op.drop_column('model_hosts', 'last_sync_status')
    op.drop_column('model_hosts', 'last_synced_at')
    op.drop_column('model_hosts', 'sync_enabled')
    op.drop_column('model_hosts', 'provider_name')
    op.drop_column('model_hosts', 'profile_name')
    op.drop_column('model_hosts', 'source_key')
    op.drop_column('model_hosts', 'source')
