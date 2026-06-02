from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


class BuilderTask(Base):
    """Link between LEGION Dashboard work items and Hermes Kanban tasks."""
    __tablename__ = "builder_tasks"

    id = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(Integer, ForeignKey("work_items.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Hermes Kanban references
    hermes_task_id = Column(String(50), nullable=False, index=True)  # e.g., "t_63114482"
    hermes_board = Column(String(100), nullable=False, default="legion-apps-build-queue")
    hermes_status = Column(String(30), nullable=False, default="triage")
    hermes_assignee = Column(String(100), nullable=True)  # e.g., "builder"
    
    # Task metadata
    title = Column(String(300), nullable=False)
    target_app = Column(String(100), nullable=True)
    target_repo = Column(String(200), nullable=True)
    priority = Column(String(20), nullable=False, default="medium")
    
    # Debate outcome snapshot
    debate_run_id = Column(Integer, nullable=True)
    recommendation = Column(String(50), nullable=True)
    implementation_readiness = Column(String(50), nullable=True)
    mandatory_edits_json = Column(Text, nullable=True)
    
    # Generated prompt (stored for audit)
    generated_prompt_snapshot = Column(Text, nullable=True)
    
    # Read-back from Hermes (updated via sync)
    pr_url = Column(String(500), nullable=True)
    branch_name = Column(String(200), nullable=True)
    merge_commit_sha = Column(String(40), nullable=True)
    result_report_path = Column(String(300), nullable=True)
    hermes_result = Column(Text, nullable=True)  # Hermes task result field
    
    # Lifecycle
    last_known_hermes_status = Column(String(30), nullable=True)
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)
    is_valid = Column(Boolean, nullable=True, default=True)  # Marks contaminated/test records invalid
    
    # Work item relationship
    work_item = relationship("WorkItem", back_populates="builder_tasks")


# Add relationship to WorkItem
# This will be patched into models.py
