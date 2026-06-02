"""add host_id columns to debate_execution_config

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-30

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add host_id columns for model host references
    op.add_column('debate_execution_config', sa.Column('default_host_id', sa.Integer(), nullable=True))
    op.add_column('debate_execution_config', sa.Column('pro_host_id', sa.Integer(), nullable=True))
    op.add_column('debate_execution_config', sa.Column('con_host_id', sa.Integer(), nullable=True))
    op.add_column('debate_execution_config', sa.Column('arbiter_host_id', sa.Integer(), nullable=True))
    op.add_column('debate_execution_config', sa.Column('fallback_host_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('debate_execution_config', 'fallback_host_id')
    op.drop_column('debate_execution_config', 'arbiter_host_id')
    op.drop_column('debate_execution_config', 'con_host_id')
    op.drop_column('debate_execution_config', 'pro_host_id')
    op.drop_column('debate_execution_config', 'default_host_id')
