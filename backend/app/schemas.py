from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class WorkItemBase(BaseModel):
    type: str = "task"
    title: str
    body: Optional[str] = None
    status: str = "draft"
    target_app: Optional[str] = None
    priority: str = "medium"
    tags: Optional[str] = None
    source: str = "operator"
    acceptance_notes: Optional[str] = None


class WorkItemCreate(WorkItemBase):
    builder_profile: Optional[str] = None
    builder_model: Optional[str] = None
    builder_provider: Optional[str] = None
    reviewer_profile: Optional[str] = None
    reviewer_model: Optional[str] = None
    reviewer_provider: Optional[str] = None


class WorkItemUpdate(BaseModel):
    type: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    status: Optional[str] = None
    target_app: Optional[str] = None
    priority: Optional[str] = None
    tags: Optional[str] = None
    source: Optional[str] = None
    acceptance_notes: Optional[str] = None
    builder_profile: Optional[str] = None
    builder_model: Optional[str] = None
    builder_provider: Optional[str] = None
    reviewer_profile: Optional[str] = None
    reviewer_model: Optional[str] = None
    reviewer_provider: Optional[str] = None
    pr_url: Optional[str] = None
    merge_commit_sha: Optional[str] = None


class WorkItemResponse(WorkItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    approved_by_operator: bool
    approval_timestamp: Optional[datetime] = None
    override_reason: Optional[str] = None
    override_timestamp: Optional[datetime] = None
    builder_profile: Optional[str] = None
    builder_model: Optional[str] = None
    builder_provider: Optional[str] = None
    reviewer_profile: Optional[str] = None
    reviewer_model: Optional[str] = None
    reviewer_provider: Optional[str] = None
    same_model_blocked: bool
    pr_url: Optional[str] = None
    merge_commit_sha: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class FollowUpCreate(BaseModel):
    severity: str = "non-blocking"
    title: Optional[str] = None
    body: Optional[str] = None
    status: str = "open"


class FollowUpResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    work_item_id: int
    severity: str
    title: Optional[str] = None
    body: Optional[str] = None
    status: str
    created_at: datetime


class BlockRequest(BaseModel):
    override_reason: Optional[str] = None


class StatsRecentItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    title: str
    status: str
    updated_at: datetime


class StatsResponse(BaseModel):
    total: int
    awaiting_approval: int
    by_status: Dict[str, int]
    by_type: Dict[str, int]
    recent: List[StatsRecentItem]


class AppResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app_id: str
    name: str
    repo: Optional[str] = None
    compose_project: str
    compose_path: str
    status: str
    last_action: Optional[str] = None
    last_result: Optional[str] = None
    last_updated: Optional[datetime] = None
    # Derived at response time from the compose file. None if the file is
    # missing or could not be parsed.
    compose_exists: Optional[bool] = None
    build_only: Optional[bool] = None
    # Runtime status from docker compose ps
    runtime_status: str = "unknown"  # running, stopped, not_created, unknown
    # Action capabilities derived from runtime status + compose info
    can_start: bool = False
    can_stop: bool = False
    can_restart: bool = False
    can_rebuild: bool = False
    can_pull: bool = False
    can_logs: bool = False
    action_unavailable_reasons: Optional[List[str]] = None
    # Web port for Open/View link. Only the port is returned; the frontend
    # computes the full URL from window.location to preserve the hostname/IP
    # the operator used to access the dashboard.
    web_port: Optional[int] = None
    can_open: bool = False
    open_unavailable_reason: Optional[str] = None


class AppActionRequest(BaseModel):
    action: str


class AppActionLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    app_id: int
    action: str
    result: str
    exit_code: Optional[int] = None
    stdout_tail: Optional[str] = None
    stderr_tail: Optional[str] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    # Concise, UI-ready summary of the outcome. The raw stdout/stderr tails
    # remain available for operators who want the full detail.
    message: Optional[str] = None
    # Progress tracking for long-running actions
    phase: str = "queued"
    elapsed_seconds: Optional[int] = None


class AppActionResult(BaseModel):
    app: AppResponse
    log: AppActionLogResponse


class AppLogsResponse(BaseModel):
    app_id: str
    lines: List[str]
    # Classification mirroring action results so the UI can show a friendly
    # empty/not-running state instead of a raw error.
    result: str = "success"
    message: Optional[str] = None
