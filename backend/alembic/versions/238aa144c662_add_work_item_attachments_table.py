"""Add work item attachments table

Revision ID: 238aa144c662
Revises: 0020
Create Date: 2026-06-01 08:22:47.782520

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '238aa144c662'
down_revision: Union[str, None] = '0020'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('work_item_attachments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('work_item_id', sa.Integer(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_work_item_attachments_id'), 'work_item_attachments', ['id'], unique=False)
    op.create_index(op.f('ix_work_item_attachments_work_item_id'), 'work_item_attachments', ['work_item_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_work_item_attachments_work_item_id'), table_name='work_item_attachments')
    op.drop_index(op.f('ix_work_item_attachments_id'), table_name='work_item_attachments')
    op.drop_table('work_item_attachments')
