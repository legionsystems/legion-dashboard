"""increase role column size in debate_arguments

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('debate_arguments', 'role',
                    existing_type=sa.String(length=60),
                    type_=sa.String(length=200),
                    existing_nullable=False)


def downgrade() -> None:
    op.alter_column('debate_arguments', 'role',
                    existing_type=sa.String(length=200),
                    type_=sa.String(length=60),
                    existing_nullable=False)
