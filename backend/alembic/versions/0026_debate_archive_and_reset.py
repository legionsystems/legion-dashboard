"""Add debate archive and reset columns.

Adds debate_archived_at and debate_reset_at to work_items for tracking
when an operator last archived/reset debate history.
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_debate_archive_and_reset"
down_revision = "0025_merge_complete_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "work_items",
        sa.Column("debate_archived_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "work_items",
        sa.Column("debate_reset_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("work_items", "debate_reset_at")
    op.drop_column("work_items", "debate_archived_at")
