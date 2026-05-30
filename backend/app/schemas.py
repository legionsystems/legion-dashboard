from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Clamped at the schema layer so both 0 and 99 are rejected with 422.
DEBATE_MIN_ROUNDS = 1
DEBATE_MAX_ROUNDS = 5
DEBATE_DEFAULT_ROUNDS = 2

ALLOWED_DEBATE_TRIGGERS = {"manual_rerun", "operator_requested"}
ALLOWED_OPERATOR_STANCES = {"pro", "con", "neutral", "auto_assign"}


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

    # System-generated/test-item classification (optional at creation)
    is_system_generated: bool = False
    is_test_item: bool = False
    generated_by: Optional[str] = None
    generated_by_prompt_id: Optional[str] = None
    source_run_id: Optional[int] = None
    source_kind: Optional[str] = None
    source_ref: Optional[str] = None


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
    
    # Archive lifecycle fields
    archived: bool = False
    archived_at: Optional[datetime] = None
    archived_by: Optional[str] = None
    archive_reason: Optional[str] = None
    
    # Populated by the router with the most recent debate run for this item,
    # or None when no debate has been queued. Used by the list page to show
    # the debate indicator without an extra round trip.
    latest_debate: Optional["DebateRunSummary"] = None


class WorkItemArchiveRequest(BaseModel):
    """Request body for archiving a work item."""
    reason: Optional[str] = None


class WorkItemClassificationUpdate(BaseModel):
    """Request body for updating work item classification."""
    is_system_generated: Optional[bool] = None
    is_test_item: Optional[bool] = None
    tags: Optional[str] = None


class BulkArchiveRequest(BaseModel):
    """Request body for bulk archiving work items."""
    ids: List[int]
    reason: Optional[str] = None


class BulkArchiveTestItemsRequest(BaseModel):
    """Request body for bulk archiving test items."""
    older_than_days: Optional[int] = None
    dry_run: bool = True


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


class DebateArgumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    debate_run_id: int
    round_number: int
    role: str
    side: str
    content: str
    created_at: datetime


class DebateRunSummary(BaseModel):
    """Run metadata without the (potentially large) argument list — used in
    list views and in the work-item-list debate indicator."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    work_item_id: int
    work_item_type_snapshot: str
    status: str
    rounds_requested: int
    rounds_completed: int
    trigger: str
    model_route: Optional[str] = None
    provenance: Optional[str] = None
    final_recommendation: Optional[str] = None
    implementation_readiness: Optional[str] = None
    summary: Optional[str] = None
    risks: Optional[str] = None
    suggested_title: Optional[str] = None
    suggested_description: Optional[str] = None
    suggested_acceptance_notes: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class DebateRunDetail(DebateRunSummary):
    arguments: List[DebateArgumentResponse] = []


class DebateRunCreate(BaseModel):
    rounds: int = Field(default=DEBATE_DEFAULT_ROUNDS)
    trigger: str = "manual_rerun"

    @field_validator("rounds")
    @classmethod
    def _clamp_rounds(cls, v: int) -> int:
        if v < DEBATE_MIN_ROUNDS or v > DEBATE_MAX_ROUNDS:
            raise ValueError(
                f"rounds must be between {DEBATE_MIN_ROUNDS} and "
                f"{DEBATE_MAX_ROUNDS}"
            )
        return v

    @field_validator("trigger")
    @classmethod
    def _check_trigger(cls, v: str) -> str:
        if v not in ALLOWED_DEBATE_TRIGGERS:
            raise ValueError(
                "trigger must be one of " + ", ".join(sorted(ALLOWED_DEBATE_TRIGGERS))
            )
        return v


class OperatorDebateInputCreate(BaseModel):
    content: str
    stance_requested: str = "auto_assign"

    @field_validator("content")
    @classmethod
    def _content_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content must not be empty")
        return v

    @field_validator("stance_requested")
    @classmethod
    def _check_stance(cls, v: str) -> str:
        if v not in ALLOWED_OPERATOR_STANCES:
            raise ValueError(
                "stance_requested must be one of "
                + ", ".join(sorted(ALLOWED_OPERATOR_STANCES))
            )
        return v


class OperatorDebateInputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    work_item_id: int
    stance_requested: str
    stance_assigned: Optional[str] = None
    content: str
    considered_in_run_id: Optional[int] = None
    created_at: datetime


class AppLogsResponse(BaseModel):
    app_id: str
    lines: List[str]
    # Classification mirroring action results so the UI can show a friendly
    # empty/not-running state instead of a raw error.
    result: str = "success"
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Debate Execution Configuration schemas
# ---------------------------------------------------------------------------


class DebateExecutionConfigResponse(BaseModel):
    """Response schema — excludes raw API key for security."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    enabled: bool
    provider: str
    base_url: str
    # Model mode: single_model (default) or role_models (advanced)
    model_mode: str
    # Single-model mode: all roles use this
    default_host_id: Optional[int] = None
    default_model: str
    # Role-specific models (only used when model_mode='role_models')
    pro_host_id: Optional[int] = None
    pro_model: Optional[str] = None
    con_host_id: Optional[int] = None
    con_model: Optional[str] = None
    arbiter_host_id: Optional[int] = None
    arbiter_model: Optional[str] = None
    fallback_host_id: Optional[int] = None
    fallback_model: Optional[str] = None
    # API key never returned — only indicate if configured
    api_key_configured: bool
    timeout_seconds: int
    max_output_chars: int
    default_rounds: int
    allow_cloud_endpoints: bool
    notes: Optional[str] = None
    updated_at: datetime


class DebateExecutionConfigUpdate(BaseModel):
    """Update schema — API key is write-only."""
    enabled: Optional[bool] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    # Model mode: single_model (default) or role_models (advanced)
    model_mode: Optional[str] = None
    # Single-model mode: all roles use this
    default_host_id: Optional[int] = None
    default_model: Optional[str] = None
    # Role-specific models (only used when model_mode='role_models')
    pro_host_id: Optional[int] = None
    pro_model: Optional[str] = None
    con_host_id: Optional[int] = None
    con_model: Optional[str] = None
    arbiter_host_id: Optional[int] = None
    arbiter_model: Optional[str] = None
    fallback_host_id: Optional[int] = None
    fallback_model: Optional[str] = None
    # Write-only: set/replace/clear API key
    api_key: Optional[str] = None
    # Explicit clear flag for API key
    clear_api_key: bool = False
    timeout_seconds: Optional[int] = None
    max_output_chars: Optional[int] = None
    default_rounds: Optional[int] = None
    allow_cloud_endpoints: Optional[bool] = None
    notes: Optional[str] = None

    @field_validator("model_mode")
    @classmethod
    def _validate_model_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("single_model", "role_models"):
            raise ValueError("model_mode must be 'single_model' or 'role_models'")
        return v

    @field_validator("default_rounds")
    @classmethod
    def _validate_rounds(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1 or v > 5):
            raise ValueError("default_rounds must be between 1 and 5")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 10 or v > 600):
            raise ValueError("timeout_seconds must be between 10 and 600")
        return v

    @field_validator("max_output_chars")
    @classmethod
    def _validate_max_chars(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and (v < 1000 or v > 100000):
            raise ValueError("max_output_chars must be between 1000 and 100000")
        return v


class DebateExecutionTestRequest(BaseModel):
    """Test connection request — may override settings temporarily."""
    base_url: Optional[str] = None
    model: Optional[str] = None  # Uses default_model if not specified
    api_key: Optional[str] = None  # Non-persistent test key
    timeout_seconds: Optional[int] = None


class DebateExecutionTestResponse(BaseModel):
    """Test connection response — safe, no secrets."""
    success: bool
    provider: str
    base_url_host: str  # Redacted host only
    model: str
    latency_ms: Optional[int] = None
    error: Optional[str] = None


# Resolve the forward reference from WorkItemResponse -> DebateRunSummary now
# that both classes are defined.
WorkItemResponse.model_rebuild()
