"""add provider capability fields

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-31

"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    # Add provider_type column
    op.add_column('model_hosts', sa.Column('provider_type', sa.String(30), nullable=False, server_default='ollama_native'))
    
    # Add capability flag columns
    op.add_column('model_hosts', sa.Column('supports_native_ollama', sa.Boolean(), nullable=True))
    op.add_column('model_hosts', sa.Column('supports_openai_chat_completions', sa.Boolean(), nullable=True))
    op.add_column('model_hosts', sa.Column('supports_model_list', sa.Boolean(), nullable=True))
    op.add_column('model_hosts', sa.Column('supports_loaded_models', sa.Boolean(), nullable=True))
    op.add_column('model_hosts', sa.Column('preferred_generation_api', sa.String(30), nullable=True))
    
    # Update last_test_status to support 'warning'
    # (PostgreSQL will handle this automatically since it's just a string)
    
    # Add last_capability_result column for JSON-safe capability test results
    op.add_column('model_hosts', sa.Column('last_capability_result', sa.Text(), nullable=True))
    
    # Update existing hosts to ollama_native if they look like Ollama
    op.execute("""
        UPDATE model_hosts 
        SET provider_type = 'ollama_native'
        WHERE (base_url LIKE '%11434%' OR base_url LIKE '%ollama%' OR base_url LIKE '%/v1')
        AND provider_type = 'ollama_native'
    """)


def downgrade():
    op.drop_column('model_hosts', 'last_capability_result')
    op.drop_column('model_hosts', 'preferred_generation_api')
    op.drop_column('model_hosts', 'supports_loaded_models')
    op.drop_column('model_hosts', 'supports_model_list')
    op.drop_column('model_hosts', 'supports_openai_chat_completions')
    op.drop_column('model_hosts', 'supports_native_ollama')
    op.drop_column('model_hosts', 'provider_type')
