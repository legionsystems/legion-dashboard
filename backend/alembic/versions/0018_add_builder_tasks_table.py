"""add builder tasks table

Revision ID: 0018
Revises: 0017
Create Date: 2026-06-01

"""
from alembic import op
import sqlalchemy as sa

revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'builder_tasks',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('work_item_id', sa.Integer(), nullable=False),
        sa.Column('hermes_task_id', sa.String(length=50), nullable=False),
        sa.Column('hermes_board', sa.String(length=100), nullable=False, default='legion-apps-build-queue'),
        sa.Column('hermes_status', sa.String(length=30), nullable=False, default='triage'),
        sa.Column('hermes_assignee', sa.String(length=100), nullable=True),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('target_app', sa.String(length=100), nullable=True),
        sa.Column('target_repo', sa.String(length=200), nullable=True),
        sa.Column('priority', sa.String(length=20), nullable=False, default='medium'),
        sa.Column('debate_run_id', sa.Integer(), nullable=True),
        sa.Column('recommendation', sa.String(length=50), nullable=True),
        sa.Column('implementation_readiness', sa.String(length=50), nullable=True),
        sa.Column('mandatory_edits_json', sa.Text(), nullable=True),
        sa.Column('generated_prompt_snapshot', sa.Text(), nullable=True),
        sa.Column('pr_url', sa.String(length=500), nullable=True),
        sa.Column('branch_name', sa.String(length=200), nullable=True),
        sa.Column('merge_commit_sha', sa.String(length=40), nullable=True),
        sa.Column('result_report_path', sa.String(length=300), nullable=True),
        sa.Column('hermes_result', sa.Text(), nullable=True),
        sa.Column('last_known_hermes_status', sa.String(length=30), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), onupdate=sa.func.now(), nullable=False),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_builder_tasks_hermes_task_id'), 'builder_tasks', ['hermes_task_id'], unique=False)
    op.create_index(op.f('ix_builder_tasks_work_item_id'), 'builder_tasks', ['work_item_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_builder_tasks_work_item_id'), table_name='builder_tasks')
    op.drop_index(op.f('ix_builder_tasks_hermes_task_id'), table_name='builder_tasks')
    op.drop_table('builder_tasks')
