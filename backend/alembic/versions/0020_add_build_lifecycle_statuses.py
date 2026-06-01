"""add build lifecycle statuses to work_items

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-01

"""
from alembic import op
import sqlalchemy as sa

revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def upgrade():
    # No schema change needed - status column is VARCHAR(30)
    # This migration documents the new status values:
    # - building: Implementation in progress (projected from Hermes running)
    # - implemented: Fully complete (projected from Hermes done)
    # - review: Implementation complete, awaiting review (projected from Hermes review)
    # These statuses are now part of the valid work item lifecycle.
    pass


def downgrade():
    # No schema change to reverse
    pass
