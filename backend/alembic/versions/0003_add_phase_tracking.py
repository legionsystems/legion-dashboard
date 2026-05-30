"""Add phase tracking to app_action_logs

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add phase column with default 'queued'
    op.add_column('app_action_logs', sa.Column('phase', sa.String(30), nullable=False, server_default='queued'))
    # Add elapsed_seconds column
    op.add_column('app_action_logs', sa.Column('elapsed_seconds', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('app_action_logs', 'elapsed_seconds')
    op.drop_column('app_action_logs', 'phase')
