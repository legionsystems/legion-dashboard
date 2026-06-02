"""Add display timezone setting

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-31 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('debate_execution_config')]
    
    if 'display_timezone' not in columns:
        op.add_column('debate_execution_config', sa.Column('display_timezone', sa.String(50), nullable=False, server_default='Australia/Sydney'))


def downgrade() -> None:
    op.drop_column('debate_execution_config', 'display_timezone')
