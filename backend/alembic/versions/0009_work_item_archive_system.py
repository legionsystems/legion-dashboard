"""add work item archive system and classification metadata

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add archive lifecycle columns to work_items
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    work_items_columns = [c['name'] for c in inspector.get_columns('work_items')]
    
    # Archive fields
    if 'archived' not in work_items_columns:
        op.add_column('work_items', sa.Column('archived', sa.Boolean(), nullable=False, server_default='false'))
    if 'archived_at' not in work_items_columns:
        op.add_column('work_items', sa.Column('archived_at', sa.DateTime(), nullable=True))
    if 'archived_by' not in work_items_columns:
        op.add_column('work_items', sa.Column('archived_by', sa.String(length=100), nullable=True))
    if 'archive_reason' not in work_items_columns:
        op.add_column('work_items', sa.Column('archive_reason', sa.Text(), nullable=True))
    
    # System-generated/test-item classification fields
    if 'is_system_generated' not in work_items_columns:
        op.add_column('work_items', sa.Column('is_system_generated', sa.Boolean(), nullable=False, server_default='false'))
    if 'is_test_item' not in work_items_columns:
        op.add_column('work_items', sa.Column('is_test_item', sa.Boolean(), nullable=False, server_default='false'))
    if 'generated_by' not in work_items_columns:
        op.add_column('work_items', sa.Column('generated_by', sa.String(length=100), nullable=True))
    if 'generated_by_prompt_id' not in work_items_columns:
        op.add_column('work_items', sa.Column('generated_by_prompt_id', sa.String(length=200), nullable=True))
    if 'source_run_id' not in work_items_columns:
        op.add_column('work_items', sa.Column('source_run_id', sa.Integer(), nullable=True))
    if 'source_kind' not in work_items_columns:
        op.add_column('work_items', sa.Column('source_kind', sa.String(length=50), nullable=True))
    if 'source_ref' not in work_items_columns:
        op.add_column('work_items', sa.Column('source_ref', sa.String(length=200), nullable=True))
    
    # Create indexes for filtering
    op.create_index(op.f('ix_work_items_archived'), 'work_items', ['archived'], unique=False)
    op.create_index(op.f('ix_work_items_is_system_generated'), 'work_items', ['is_system_generated'], unique=False)
    op.create_index(op.f('ix_work_items_is_test_item'), 'work_items', ['is_test_item'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_work_items_is_test_item'), table_name='work_items')
    op.drop_index(op.f('ix_work_items_is_system_generated'), table_name='work_items')
    op.drop_index(op.f('ix_work_items_archived'), table_name='work_items')
    
    op.drop_column('work_items', 'source_ref')
    op.drop_column('work_items', 'source_kind')
    op.drop_column('work_items', 'source_run_id')
    op.drop_column('work_items', 'generated_by_prompt_id')
    op.drop_column('work_items', 'generated_by')
    op.drop_column('work_items', 'is_test_item')
    op.drop_column('work_items', 'is_system_generated')
    op.drop_column('work_items', 'archive_reason')
    op.drop_column('work_items', 'archived_by')
    op.drop_column('work_items', 'archived_at')
    op.drop_column('work_items', 'archived')
