"""Add is_valid flag to builder_tasks

Revision ID: 0019
Revises: 0018
Create Date: 2026-06-01

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add is_valid column with default True
    op.add_column('builder_tasks', sa.Column('is_valid', sa.Boolean(), nullable=False, server_default='true'))


def downgrade() -> None:
    op.drop_column('builder_tasks', 'is_valid')
