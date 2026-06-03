"""add debate_arbiter_rerun_count

Revision ID: e2c8e08bb733
Revises: 0026_debate_archive_and_reset
Create Date: 2026-06-03 07:40:04.173505

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e2c8e08bb733'
down_revision: Union[str, None] = '0026_debate_archive_and_reset'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "debate_runs",
        sa.Column("arbiter_rerun_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("debate_runs", "arbiter_rerun_count")
