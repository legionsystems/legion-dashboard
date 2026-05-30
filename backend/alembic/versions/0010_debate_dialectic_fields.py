"""add dialectic fields to debate_arguments

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-30

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add dialectic tracking columns to debate_arguments
    op.add_column('debate_arguments', sa.Column('claim_id', sa.String(length=50), nullable=True))
    op.add_column('debate_arguments', sa.Column('responds_to_claim_ids', sa.Text(), nullable=True))
    op.add_column('debate_arguments', sa.Column('concession', sa.Text(), nullable=True))
    op.add_column('debate_arguments', sa.Column('rebuttal', sa.Text(), nullable=True))
    op.add_column('debate_arguments', sa.Column('revised_position', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('debate_arguments', 'revised_position')
    op.drop_column('debate_arguments', 'rebuttal')
    op.drop_column('debate_arguments', 'concession')
    op.drop_column('debate_arguments', 'responds_to_claim_ids')
    op.drop_column('debate_arguments', 'claim_id')
