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

    # Workflow lifecycle metadata (slice 1).
    # `effective_state` is the projected/derived state computed by
    # `app.lifecycle.compute_effective_state` at response time. The other
    # fields are persisted nullable columns that later slices will populate.
    dashboard_lifecycle_status: Optional[str] = None
    effective_state: Optional[str] = None
    pr_number: Optional[int] = None
    code_review_status: Optional[str] = None
    branch_name: Optional[str] = None
    preview_required: Optional[bool] = None
    preview_deployed: Optional[bool] = None
    operator_certified: Optional[bool] = None
    ready_to_merge: Optional[bool] = None
    certified_at: Optional[datetime] = None
    certified_by: Optional[str] = None
    certification_note: Optional[str] = None

    # Operator rejection / change-request metadata (slice 2).
    rejected_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    changes_requested_at: Optional[datetime] = None
    changes_requested_by: Optional[str] = None
    change_request: Optional[str] = None

    # Preview deployment metadata (slice 4). Populated by the
    # ``deploy-preview`` action and cleared/stamped by ``revert-preview``.
    preview_status: Optional[str] = None
    preview_url: Optional[str] = None
    preview_branch: Optional[str] = None
    preview_pr_number: Optional[int] = None
    preview_commit_sha: Optional[str] = None
    preview_deployed_at: Optional[datetime] = None
    preview_deployed_by: Optional[str] = None
    preview_health_status: Optional[str] = None
    preview_error: Optional[str] = None
    preview_reverted_at: Optional[datetime] = None
    preview_reverted_by: Optional[str] = None
    preview_revert_reason: Optional[str] = None

    # Merge / complete metadata (slice 5). Populated by ``merge`` and
    # ``complete`` endpoints.
    merge_status: Optional[str] = None
    merged_at: Optional[datetime] = None
    merged_by: Optional[str] = None
    merge_note: Optional[str] = None
    merge_error: Optional[str] = None
    post_merge_verified_at: Optional[datetime] = None
    post_merge_verified_by: Optional[str] = None
    post_merge_health_status: Optional[str] = None
    completed_at: Optional[datetime] = None
    completed_by: Optional[str] = None
    completion_note: Optional[str] = None

    # Archive lifecycle fields
    archived: bool = False
    archived_at: Optional[datetime] = None
    archived_by: Optional[str] = None
    archive_reason: Optional[str] = None

    # Debate archive/reset tracking
    debate_archived_at: Optional[datetime] = None
    debate_reset_at: Optional[datetime] = None

    # Populated by the router with the most recent debate run for this item,
    # or None when no debate has been queued. Used by the list page to show
    # the debate indicator without an extra round trip.
    latest_debate: Optional["DebateRunSummary"] = None


class CertifyRequest(BaseModel):
    """Operator marks a Work Item as certified (ready to merge gate)."""
    certification_note: Optional[str] = None


class RejectRequest(BaseModel):
    """Operator rejects a Work Item outright — abandon the work."""
    rejection_reason: str

    @field_validator("rejection_reason")
    @classmethod
    def _reason_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("rejection_reason must not be empty")
        return v


class RejectWithChangesRequest(BaseModel):
    """Operator sends a Work Item back with a change request — needs rework."""
    change_request: str

    @field_validator("change_request")
    @classmethod
    def _change_request_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("change_request must not be empty")
        return v


class WorkItemArchiveRequest(BaseModel):
    """Request body for archiving a work item."""
    reason: Optional[str] = None


class WorkItemClassificationUpdate(BaseModel):
    """Request body for updating work item classification."""
    is_system_generated: Optional[bool] = None
    is_test_item: Optional[bool] = None
    tags: Optional[str] = None


# ---------------------------------------------------------------------------
# Attachment schemas
# ---------------------------------------------------------------------------


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    work_item_id: int
    filename: str
    original_filename: str
    content_type: str
    file_size: int
    created_at: datetime


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
    claim_id: Optional[str] = None
    responds_to_claim_ids: Optional[List[str]] = None
    concession: Optional[str] = None
    rebuttal: Optional[str] = None
    revised_position: Optional[str] = None
    created_at: datetime

    @field_validator('responds_to_claim_ids', mode='before')
    @classmethod
    def parse_responds_to_claim_ids(cls, v):
        if v is None:
            return None
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except (json.JSONDecodeError, TypeError):
                return None
        return None


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
    # Execution stage tracking
    execution_stage: Optional[str] = None
    current_round: Optional[int] = None
    current_turn: Optional[str] = None
    current_side: Optional[str] = None
    current_role: Optional[str] = None
    current_model: Optional[str] = None
    last_progress_at: Optional[datetime] = None
    progress_message: Optional[str] = None
    warmup_started_at: Optional[datetime] = None
    warmup_completed_at: Optional[datetime] = None
    warmup_duration_ms: Optional[int] = None
    warmup_method: Optional[str] = None
    warmup_error: Optional[str] = None
    generation_started_at: Optional[datetime] = None
    generation_completed_at: Optional[datetime] = None
    generation_duration_ms: Optional[int] = None
    error_type: Optional[str] = None
    error_stage: Optional[str] = None
    error_round: Optional[int] = None
    error_turn: Optional[str] = None
    error_elapsed_ms: Optional[int] = None
    # Worker fields
    worker_status: Optional[str] = None
    worker_id: Optional[str] = None
    heartbeat_at: Optional[datetime] = None
    lease_until: Optional[datetime] = None
    cancel_requested: bool = False
    # Cleanup/visibility controls
    hidden_at: Optional[datetime] = None
    hidden_by: Optional[str] = None
    hidden_reason: Optional[str] = None
    hidden_category: Optional[str] = None
    is_test_run: bool = False
    superseded_by_run_id: Optional[int] = None
    cleanup_note: Optional[str] = None
    # Arbiter rerun tracking
    arbiter_rerun_count: int = 0


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
    # Warmup settings
    warm_model_before_debate: bool = True
    warmup_timeout_seconds: int = 300
    keep_model_loaded_for: str = "1h"
    fail_debate_if_warmup_fails: bool = True
    # Failed run display
    visible_failed_runs_limit: int = 2
    # Worker configuration
    execution_backend: str = "worker"
    worker_enabled: bool = True
    worker_poll_interval_seconds: int = 3
    worker_lease_seconds: int = 300
    worker_heartbeat_seconds: int = 10
    worker_max_concurrent_runs: int = 1
    turn_timeout_seconds: Optional[int] = None
    whole_run_timeout_seconds: Optional[int] = None
    retry_failed_turn_enabled: bool = True
    max_turn_retries: int = 1
    # Display settings
    display_timezone: str = "Australia/Sydney"
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
    # Display settings
    display_timezone: Optional[str] = None
    # Warmup settings
    warm_model_before_debate: Optional[bool] = None
    warmup_timeout_seconds: Optional[int] = None
    keep_model_loaded_for: Optional[str] = None
    fail_debate_if_warmup_fails: Optional[bool] = None
    # Failed run display
    visible_failed_runs_limit: Optional[int] = None
    # Worker configuration
    execution_backend: Optional[str] = None
    worker_enabled: Optional[bool] = None
    worker_poll_interval_seconds: Optional[int] = None
    worker_lease_seconds: Optional[int] = None
    worker_heartbeat_seconds: Optional[int] = None
    worker_max_concurrent_runs: Optional[int] = None
    turn_timeout_seconds: Optional[int] = None
    whole_run_timeout_seconds: Optional[int] = None
    retry_failed_turn_enabled: Optional[bool] = None
    max_turn_retries: Optional[int] = None
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


# ---------------------------------------------------------------------------
# Debate run cleanup/visibility schemas
# ---------------------------------------------------------------------------


class DebateRunHideRequest(BaseModel):
    """Request to hide a debate run."""
    reason: Optional[str] = None
    category: str = "operator_cleanup"


class DebateRunRestoreRequest(BaseModel):
    """Request to restore a hidden debate run."""
    pass


class DebateRunBulkHideRequest(BaseModel):
    """Request to bulk hide failed debate runs for a work item."""
    older_than_run_id: Optional[int] = None
    keep_latest_failed: bool = True
    reason: Optional[str] = None


class DebateRunHideResponse(BaseModel):
    """Response for hide/restore operations."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    work_item_id: int
    status: str
    hidden_at: Optional[datetime] = None
    hidden_by: Optional[str] = None
    hidden_reason: Optional[str] = None
    hidden_category: Optional[str] = None


# ---------------------------------------------------------------------------
# Debate reset / archive schemas
# ---------------------------------------------------------------------------


class DebateResetRequest(BaseModel):
    """Request to reset/archive all active debate runs for a work item."""
    reason: Optional[str] = None
    mode: str = "archive"  # archive (soft-hide) or hard_delete (destructive)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, v: str) -> str:
        if v not in ("archive", "hard_delete"):
            raise ValueError("mode must be 'archive' or 'hard_delete'")
        return v


class DebateResetResponse(BaseModel):
    """Response for debate reset operation."""
    work_item_id: int
    archived_count: int
    hard_deleted: bool
    archived_run_ids: list[int]
    message: str


# ---------------------------------------------------------------------------
# Model warmup schemas
# ---------------------------------------------------------------------------


class ModelWarmupRequest(BaseModel):
    """Request to warm up a model."""
    keep_alive: Optional[str] = "1h"
    timeout_seconds: Optional[int] = 300


class ModelWarmupResponse(BaseModel):
    """Response for model warmup operations."""
    success: bool
    provider: Optional[str] = None
    base_url_host: Optional[str] = None
    model: str
    latency_ms: Optional[int] = None
    warmup_method: Optional[str] = None  # ollama_native, openai_compatible_ping, skipped
    error: Optional[str] = None
    # Residency verification (Ollama native only)
    model_resident: Optional[bool] = None  # true/false/unknown
    expires_at: Optional[str] = None  # ISO timestamp if resident


# ---------------------------------------------------------------------------
# Model host schemas
# ---------------------------------------------------------------------------


class ModelHostModelResponse(BaseModel):
    """Model in a host's catalog."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    model_id: str
    display_name: Optional[str] = None
    is_available: bool = True
    discovered_at: datetime


class ModelHostResponse(BaseModel):
    """Model host configuration response — masks secrets."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    name: str
    provider_type: str  # ollama_native, openai_compatible, xai, other
    provider: str  # Legacy field
    base_url: str
    enabled: bool
    allow_cloud_endpoints: bool
    api_key_configured: bool
    
    # Capability flags
    supports_native_ollama: Optional[bool] = None
    supports_openai_chat_completions: Optional[bool] = None
    supports_model_list: Optional[bool] = None
    supports_loaded_models: Optional[bool] = None
    preferred_generation_api: Optional[str] = None
    
    # Test status
    last_test_status: Optional[str] = None
    last_test_message: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    last_models_refresh_at: Optional[datetime] = None
    last_error: Optional[str] = None
    
    # Models (eager loaded)
    models: List[ModelHostModelResponse] = []
    
    # Hermes sync metadata
    source: str = "manual"
    source_key: Optional[str] = None
    profile_name: Optional[str] = None
    provider_name: Optional[str] = None
    sync_enabled: bool = True
    last_synced_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_sync_error: Optional[str] = None
    
    created_at: datetime
    updated_at: datetime


class ModelHostCreate(BaseModel):
    """Create a new model host."""
    name: str
    provider_type: str = "ollama_native"
    provider: str = "openai_compatible"  # Legacy
    base_url: str
    api_key: Optional[str] = None
    enabled: bool = True
    allow_cloud_endpoints: bool = False


class ModelHostUpdate(BaseModel):
    """Update a model host."""
    name: Optional[str] = None
    provider_type: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    clear_api_key: bool = False
    enabled: Optional[bool] = None
    allow_cloud_endpoints: Optional[bool] = None
    supports_native_ollama: Optional[bool] = None
    supports_openai_chat_completions: Optional[bool] = None
    supports_model_list: Optional[bool] = None
    supports_loaded_models: Optional[bool] = None
    preferred_generation_api: Optional[str] = None


class CapabilityCheckResult(BaseModel):
    """Result of a single capability check."""
    name: str
    status: str  # success, failed, skipped
    endpoint: Optional[str] = None
    latency_ms: Optional[int] = None
    message: Optional[str] = None


class ModelHostCapabilityTestResponse(BaseModel):
    """Structured capability test result for a model host."""
    provider_id: int
    provider_type: str
    enabled: bool
    overall_status: str  # success, warning, failed
    checks: List[CapabilityCheckResult] = []
    recommended_generation_api: Optional[str] = None  # ollama_native, openai_chat_completions
    safe_error: Optional[str] = None
    selected_model_available: Optional[bool] = None
    selected_model: Optional[str] = None


# ---------------------------------------------------------------------------
# Repo safety / lock schemas (slice 3)
# ---------------------------------------------------------------------------


class RepoLockResponse(BaseModel):
    """Serialized RepoLock row."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    repo_path: str
    repo_name: str
    work_item_id: Optional[int] = None
    task_id: Optional[str] = None
    branch_name: str
    commit_sha: str
    lock_owner: Optional[str] = None
    lock_status: str
    started_at: datetime
    released_at: Optional[datetime] = None
    release_reason: Optional[str] = None


class RepoSafetyResult(BaseModel):
    """Outcome of inspecting a repo before starting a builder run."""

    repo_path: str
    is_clean: bool
    dirty_files: List[str] = []
    staged_files: List[str] = []
    untracked_files: List[str] = []
    current_branch: Optional[str] = None
    current_commit: Optional[str] = None
    # When ``is_clean`` is False, ``blocker_code`` carries the machine-readable
    # gate reason (e.g. "blocked_dirty_repo") and ``blocker_message`` carries
    # a human-readable summary suitable for UI display.
    blocker_code: Optional[str] = None
    blocker_message: Optional[str] = None


class LockAcquireRequest(BaseModel):
    """Internal/manual request to acquire a repo lock."""

    repo_path: str
    repo_name: str
    branch_name: str
    commit_sha: str
    work_item_id: Optional[int] = None
    task_id: Optional[str] = None
    lock_owner: Optional[str] = None


class LockAcquireResponse(BaseModel):
    """Result of a lock-acquire attempt."""

    acquired: bool
    lock: Optional[RepoLockResponse] = None
    existing_lock: Optional[RepoLockResponse] = None
    blocker_code: Optional[str] = None
    blocker_message: Optional[str] = None


class LockReleaseRequest(BaseModel):
    """Operator/system request to release a repo lock."""

    release_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Preview deployment / revert schemas (slice 4)
# ---------------------------------------------------------------------------


class DeployPreviewRequest(BaseModel):
    """Operator-initiated request to deploy a Work Item's PR as a preview.

    The router resolves the branch, PR, and target repo from the Work Item
    itself; the operator only supplies optional attribution. ``deployed_by``
    is recorded in ``preview_deployed_by`` so the audit trail names a human.
    """

    deployed_by: Optional[str] = None


class MergeRequest(BaseModel):
    """Operator-initiated request to merge a certified Work Item's PR.

    The router validates that the Work Item carries the PR metadata it
    expects (``expected_pr_number``, ``expected_branch``, ``expected_base_branch``)
    so a stale UI cannot ask the executor to merge a different PR. The
    ``merge_note`` is recorded on the Work Item alongside the merge SHA.
    ``merged_by`` is optional operator attribution.
    """

    merge_note: Optional[str] = None
    expected_pr_number: int
    expected_branch: str
    expected_base_branch: str
    merged_by: Optional[str] = None

    @field_validator("expected_branch", "expected_base_branch")
    @classmethod
    def _nonempty_branch(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v


class CompleteRequest(BaseModel):
    """Operator-initiated request to mark a merged Work Item as complete.

    Requires that the Work Item has already been merged and its post-merge
    healthcheck succeeded. ``completion_note`` and ``completed_by`` are
    recorded for audit.
    """

    completion_note: Optional[str] = None
    completed_by: Optional[str] = None


class VerifyMergeRequest(BaseModel):
    """Operator-initiated re-verification of a failed post-merge deploy.

    Used after the operator has manually fixed a deploy that landed in
    ``merged_deployment_failed``. The router re-runs the host executor's
    healthcheck and, on success, flips the Work Item back to ``merged``
    without re-merging the PR. ``verified_by`` is optional attribution.
    """

    verified_by: Optional[str] = None


class RevertPreviewRequest(BaseModel):
    """Operator-initiated request to revert a deployed preview.

    ``reason`` is required so reverts always carry an explanation in the
    audit trail. ``reverted_by`` is optional attribution.
    """

    reason: str
    reverted_by: Optional[str] = None

    @field_validator("reason")
    @classmethod
    def _reason_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reason must not be empty")
        return v


# ---------------------------------------------------------------------------
# Host-side preview executor schemas (slice 4b)
# ---------------------------------------------------------------------------
#
# The dashboard container does not have write access to ``/srv/repo`` and
# cannot run ``docker compose`` against the host's daemon for arbitrary
# branches. Slice 4b moves the side-effectful work to a host-side executor
# (``legion-preview-executor``) reachable at
# ``http://host.docker.internal:8766/preview``. These schemas describe the
# request the dashboard sends and the response it parses, so both ends
# agree on the wire shape and the router can stamp metadata only on a
# confirmed success.


# Actions the dashboard may request from the host executor.
ALLOWED_EXECUTOR_ACTIONS = {
    "deploy_preview",
    "revert_preview",
    "merge_pr",
    "healthcheck",
    # Read-only worktree inspection that backs the Start Build safety gate
    # — the dashboard container's /srv/repo mount isn't a git worktree, so
    # the gate has to ask the host executor what state the worktree is in.
    "repo_safety_check",
}


class PreviewExecutorRequest(BaseModel):
    """Wire payload sent from the dashboard to the host preview executor.

    ``action`` selects deploy vs revert vs merge vs inspection. ``repo_path``
    is validated against a host-side allowlist before any work runs — the
    dashboard never sends an arbitrary path, but the executor refuses
    unknown paths defensively. ``branch`` is the git ref to check out on
    the host worktree (or the base branch for a merge action); pure
    inspection actions (``healthcheck``, ``repo_safety_check``) omit it.
    ``service`` is the docker-compose service to rebuild/restart (defaults
    to the dashboard's app service). For ``merge_pr``, ``pr_number`` and
    ``base_branch`` are required so the executor can confirm the merge
    target matches what the dashboard expects.
    """

    action: str
    repo_path: str
    branch: Optional[str] = None
    service: Optional[str] = None
    pr_number: Optional[int] = None
    base_branch: Optional[str] = None

    @field_validator("action")
    @classmethod
    def _action_allowed(cls, v: str) -> str:
        if v not in ALLOWED_EXECUTOR_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(ALLOWED_EXECUTOR_ACTIONS)}"
            )
        return v

    @field_validator("repo_path")
    @classmethod
    def _repo_path_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v

    @field_validator("branch")
    @classmethod
    def _branch_nonempty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.strip():
            raise ValueError("must not be empty")
        return v


class PreviewExecutorResponse(BaseModel):
    """Response the host executor returns to the dashboard.

    ``success`` is the single boolean the router gates metadata writes on.
    On a non-success the router must release the lock with a failed status
    and not stamp ``preview_deployed = True``. ``error_code`` is a stable
    short identifier the operator UI can localize; ``error`` is the human
    string.
    """

    success: bool
    action: Optional[str] = None
    commit_sha: Optional[str] = None
    branch: Optional[str] = None
    health_status: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    # ``merge_pr`` adds the resulting merge SHA so the dashboard can stamp
    # ``WorkItem.merge_commit_sha`` only on confirmed success.
    merge_commit_sha: Optional[str] = None


# Resolve the forward reference from WorkItemResponse -> DebateRunSummary now
# that both classes are defined.
WorkItemResponse.model_rebuild()
