"""Builder Task schemas for Hermes Kanban bridge."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class BuilderTaskBase(BaseModel):
    """Base schema for builder tasks."""
    work_item_id: int
    title: str
    target_app: Optional[str] = None
    target_repo: Optional[str] = None
    priority: str = "medium"


class BuilderTaskCreate(BuilderTaskBase):
    """Schema for creating a builder task."""
    debate_run_id: Optional[int] = None
    recommendation: Optional[str] = None
    implementation_readiness: Optional[str] = None
    mandatory_edits_json: Optional[str] = None
    hermes_assignee: Optional[str] = None  # e.g., "builder"


class BuilderTaskResponse(BuilderTaskBase):
    """Schema for builder task response."""
    id: int
    hermes_task_id: str
    hermes_board: str
    hermes_status: str
    hermes_assignee: Optional[str] = None
    debate_run_id: Optional[int] = None
    recommendation: Optional[str] = None
    implementation_readiness: Optional[str] = None
    pr_url: Optional[str] = None
    branch_name: Optional[str] = None
    merge_commit_sha: Optional[str] = None
    hermes_result: Optional[str] = None
    last_known_hermes_status: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SendToBuilderRequest(BaseModel):
    """Request to send a work item to builder."""
    hermes_assignee: Optional[str] = Field(None, description="Hermes profile to assign (e.g., 'builder')")
    target_repo: Optional[str] = Field(None, description="Target repo path (auto-derived if not provided)")
    priority: Optional[str] = Field(None, description="Priority override")
