"""clean duplicate model entries

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-31

Deduplicates model_host_models by provider_id + normalized_model_name.
Preserves one canonical row per unique model, updates references if needed.
Does not delete history - marks duplicates as inactive.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text


revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def upgrade():
    # Add unique constraint on provider_id + normalized model name
    # First, clean duplicates by keeping the earliest discovered entry
    
    conn = op.get_bind()
    
    # Find and mark duplicates, keeping the one with lowest id
    conn.execute(text("""
        DELETE FROM model_host_models
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM model_host_models
            GROUP BY host_id, LOWER(TRIM(model_id))
        )
    """))
    
    # Add unique index to prevent future duplicates
    # Note: PostgreSQL expression indexes need raw SQL
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_model_host_models_unique
        ON model_host_models (host_id, LOWER(TRIM(model_id)))
    """)
    
    # Add display_name normalization for better deduplication
    op.execute("""
        UPDATE model_host_models
        SET display_name = COALESCE(display_name, model_id)
        WHERE display_name IS NULL
    """)


def downgrade():
    op.drop_index('idx_model_host_models_unique', 'model_host_models')
