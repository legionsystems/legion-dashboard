from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .database import Base


# Statuses that auto-queue a debate run when a work item enters them.
DEBATE_ELIGIBLE_STATUSES = frozenset(
    {
        "draft",
        "awaiting_approval",
        "pending_approval",
        "review",
        "review_needed",
        "ready_for_approval",
    }
)


class WorkItem(Base):
    __tablename__ = "work_items"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(30), nullable=False, default="task")
    title = Column(String(300), nullable=False)
    body = Column(Text, nullable=True)
    status = Column(String(30), nullable=False, default="draft")

    # Intake metadata
    target_app = Column(String(100), nullable=True)
    priority = Column(String(20), nullable=False, default="medium")
    tags = Column(String(500), nullable=True)
    source = Column(String(30), nullable=False, default="operator")
    acceptance_notes = Column(Text, nullable=True)

    approved_by_operator = Column(Boolean, nullable=False, default=False)
    approval_timestamp = Column(DateTime, nullable=True)
    override_reason = Column(Text, nullable=True)
    override_timestamp = Column(DateTime, nullable=True)

    builder_profile = Column(String(100), nullable=True)
    builder_model = Column(String(200), nullable=True)
    builder_provider = Column(String(100), nullable=True)
    reviewer_profile = Column(String(100), nullable=True)
    reviewer_model = Column(String(200), nullable=True)
    reviewer_provider = Column(String(100), nullable=True)
    same_model_blocked = Column(Boolean, nullable=False, default=False)

    pr_url = Column(String(500), nullable=True)
    merge_commit_sha = Column(String(40), nullable=True)

    # Workflow lifecycle metadata (slice 1: foundation).
    # All columns are nullable; later slices populate them as operators
    # certify, repos lock, previews deploy, and merges complete.
    dashboard_lifecycle_status = Column(String(40), nullable=True)
    effective_state = Column(String(40), nullable=True)
    pr_number = Column(Integer, nullable=True)
    code_review_status = Column(String(30), nullable=True)
    branch_name = Column(String(200), nullable=True)
    preview_required = Column(Boolean, nullable=True)
    preview_deployed = Column(Boolean, nullable=True)
    operator_certified = Column(Boolean, nullable=True)
    ready_to_merge = Column(Boolean, nullable=True)
    certified_at = Column(DateTime, nullable=True)
    certified_by = Column(String(100), nullable=True)
    certification_note = Column(Text, nullable=True)

    # Operator rejection / change-request metadata (slice 2).
    rejected_at = Column(DateTime, nullable=True)
    rejected_by = Column(String(100), nullable=True)
    rejection_reason = Column(Text, nullable=True)
    changes_requested_at = Column(DateTime, nullable=True)
    changes_requested_by = Column(String(100), nullable=True)
    change_request = Column(Text, nullable=True)

    # Preview deployment / revert metadata (slice 4). All nullable; the
    # ``deploy-preview`` action populates them, and ``revert-preview`` clears
    # ``preview_deployed`` and stamps the revert columns. ``preview_required``
    # and ``preview_deployed`` already exist above from slice 1; this block
    # adds the rest of the observable preview state.
    preview_status = Column(String(30), nullable=True)
    preview_url = Column(String(500), nullable=True)
    preview_branch = Column(String(200), nullable=True)
    preview_pr_number = Column(Integer, nullable=True)
    preview_commit_sha = Column(String(40), nullable=True)
    preview_deployed_at = Column(DateTime, nullable=True)
    preview_deployed_by = Column(String(100), nullable=True)
    preview_health_status = Column(String(30), nullable=True)
    preview_error = Column(Text, nullable=True)
    preview_reverted_at = Column(DateTime, nullable=True)
    preview_reverted_by = Column(String(100), nullable=True)
    preview_revert_reason = Column(Text, nullable=True)

    # Merge / complete metadata (slice 5). The ``merge`` action records when
    # and how a PR landed; ``post_merge_verified_*`` carries the post-merge
    # healthcheck result; ``complete`` records the operator's terminal
    # acknowledgement after a clean post-merge deploy. All nullable so prior
    # rows are unaffected.
    merge_status = Column(String(30), nullable=True)
    merged_at = Column(DateTime, nullable=True)
    merged_by = Column(String(100), nullable=True)
    merge_note = Column(Text, nullable=True)
    merge_error = Column(Text, nullable=True)
    post_merge_verified_at = Column(DateTime, nullable=True)
    post_merge_verified_by = Column(String(100), nullable=True)
    post_merge_health_status = Column(String(30), nullable=True)
    completed_at = Column(DateTime, nullable=True)
    completed_by = Column(String(100), nullable=True)
    completion_note = Column(Text, nullable=True)

    # Archive lifecycle
    archived = Column(Boolean, nullable=False, default=False, index=True)
    archived_at = Column(DateTime, nullable=True)
    archived_by = Column(String(100), nullable=True)
    archive_reason = Column(Text, nullable=True)

    # Debate archive/reset tracking
    debate_archived_at = Column(DateTime, nullable=True)
    debate_reset_at = Column(DateTime, nullable=True)

    # System-generated/test-item metadata
    is_system_generated = Column(Boolean, nullable=False, default=False, index=True)
    is_test_item = Column(Boolean, nullable=False, default=False, index=True)
    generated_by = Column(String(100), nullable=True)
    generated_by_prompt_id = Column(String(200), nullable=True)
    source_run_id = Column(Integer, nullable=True)
    source_kind = Column(String(50), nullable=True)
    source_ref = Column(String(200), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    follow_ups = relationship(
        "FollowUp", back_populates="work_item", cascade="all, delete-orphan"
    )
    builder_tasks = relationship(
        "BuilderTask", back_populates="work_item", cascade="all, delete-orphan"
    )
    attachments = relationship(
        "WorkItemAttachment", back_populates="work_item", cascade="all, delete-orphan"
    )


class WorkItemAttachment(Base):
    __tablename__ = "work_item_attachments"

    id = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(
        Integer,
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    original_filename = Column(String(255), nullable=False)
    content_type = Column(String(100), nullable=False)
    file_size = Column(Integer, nullable=False)
    storage_path = Column(String(500), nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    work_item = relationship("WorkItem", back_populates="attachments")


class FollowUp(Base):
    __tablename__ = "follow_ups"

    id = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(
        Integer,
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    severity = Column(String(20), nullable=False, default="non-blocking")
    title = Column(String(300), nullable=True)
    body = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="open")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    work_item = relationship("WorkItem", back_populates="follow_ups")


class App(Base):
    __tablename__ = "apps"

    id = Column(Integer, primary_key=True, index=True)
    app_id = Column(String(100), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    repo = Column(String(500), nullable=True)
    compose_project = Column(String(100), nullable=False)
    compose_path = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, default="unknown")
    last_action = Column(String(20), nullable=True)
    last_result = Column(String(20), nullable=True)
    last_updated = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    action_logs = relationship(
        "AppActionLog", back_populates="app", cascade="all, delete-orphan"
    )


class DebateRun(Base):
    __tablename__ = "debate_runs"

    id = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(
        Integer,
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Snapshot of the work item's type at the time the run was created. Kept
    # separate so historical runs remain legible even if the item's type is
    # later edited (which the API allows).
    work_item_type_snapshot = Column(String(30), nullable=False)

    # Status: queued | claimed | warming | generating | running | completed | failed | cancelled
    status = Column(String(20), nullable=False, default="queued")
    
    # Worker/queue fields for durable execution
    worker_status = Column(String(20), nullable=False, default="queued")  # queued | claimed | warming | running | completed | failed | cancelled
    worker_id = Column(String(100), nullable=True)  # Worker that claimed this run
    lease_until = Column(DateTime, nullable=True)  # Lease expiration time
    claimed_at = Column(DateTime, nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    queued_at = Column(DateTime, server_default=func.now(), nullable=False)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancelled_by = Column(String(100), nullable=True)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    retry_of_run_id = Column(Integer, nullable=True)  # Link to previous run if this is a retry
    retry_from_turn_id = Column(Integer, nullable=True)  # Retry from specific turn
    attempt_number = Column(Integer, nullable=False, default=1)
    max_attempts = Column(Integer, nullable=False, default=1)
    arbiter_rerun_count = Column(Integer, nullable=False, default=0)  # Number of arbiter-only reruns

    # Operator-controlled (1..5, default 2). The router enforces the clamp;
    # the column trusts the router and the model layer's pre-write validation.
    rounds_requested = Column(Integer, nullable=False, default=2)
    rounds_completed = Column(Integer, nullable=False, default=0)

    # automatic | manual_rerun | operator_requested
    trigger = Column(String(30), nullable=False, default="automatic")

    # Free-form provenance string identifying the model route used (or empty
    # if the execution bridge was not configured).
    model_route = Column(String(200), nullable=True)
    provenance = Column(Text, nullable=True)

    # Snapshot of the work item's debate-relevant fields at the moment this
    # run was queued. Used to de-duplicate automatic re-runs against an
    # unchanged item.
    input_snapshot_json = Column(Text, nullable=True)
    changed_since_previous_json = Column(Text, nullable=True)

    # Final arbiter output. NULL until the run completes successfully.
    final_recommendation = Column(String(40), nullable=True)
    implementation_readiness = Column(String(30), nullable=True)
    summary = Column(Text, nullable=True)
    risks = Column(Text, nullable=True)
    suggested_title = Column(String(300), nullable=True)
    suggested_description = Column(Text, nullable=True)
    suggested_acceptance_notes = Column(Text, nullable=True)

    error_message = Column(Text, nullable=True)

    # Execution stage tracking (warming, running, etc.)
    execution_stage = Column(String(20), nullable=True)  # queued | warming | generating | running | completed | failed
    current_round = Column(Integer, nullable=True)  # Active round during execution
    current_turn = Column(String(50), nullable=True)  # Active turn name (e.g., "pro_opening", "con_response")
    current_side = Column(String(10), nullable=True)  # pro | con | arbiter
    current_role = Column(String(100), nullable=True)  # Active role during execution
    current_model = Column(String(200), nullable=True)  # Model being used
    last_progress_at = Column(DateTime, nullable=True)
    progress_message = Column(Text, nullable=True)
    warmup_started_at = Column(DateTime, nullable=True)
    warmup_completed_at = Column(DateTime, nullable=True)
    warmup_duration_ms = Column(Integer, nullable=True)
    warmup_method = Column(String(50), nullable=True)  # ollama_native | openai_compatible_ping
    warmup_error = Column(Text, nullable=True)
    generation_started_at = Column(DateTime, nullable=True)
    generation_completed_at = Column(DateTime, nullable=True)
    generation_duration_ms = Column(Integer, nullable=True)
    error_type = Column(String(50), nullable=True)
    error_stage = Column(String(20), nullable=True)  # warmup | generation
    error_round = Column(Integer, nullable=True)
    error_turn = Column(String(50), nullable=True)
    error_elapsed_ms = Column(Integer, nullable=True)

    # Cleanup/visibility controls
    hidden_at = Column(DateTime, nullable=True)
    hidden_by = Column(String(100), nullable=True)
    hidden_reason = Column(Text, nullable=True)
    hidden_category = Column(String(50), nullable=True)  # repeated_timeout | setup_failure | superseded | operator_cleanup | test_run
    is_test_run = Column(Boolean, nullable=False, default=False)
    superseded_by_run_id = Column(Integer, nullable=True)
    cleanup_note = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)

    arguments = relationship(
        "DebateArgument",
        back_populates="debate_run",
        cascade="all, delete-orphan",
        order_by="DebateArgument.id",
    )


class DebateArgument(Base):
    __tablename__ = "debate_arguments"

    id = Column(Integer, primary_key=True, index=True)
    debate_run_id = Column(
        Integer,
        ForeignKey("debate_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    round_number = Column(Integer, nullable=False, default=1)

    # Role names follow the spec: Product Owner, UX/Design Reviewer,
    # Technical Architect, Security/Privacy Reviewer, Builder,
    # Skeptic/Red Team, Final Arbiter, Operator (for operator-attached args).
    role = Column(String(200), nullable=False)
    # pro | con | neutral | arbiter
    side = Column(String(20), nullable=False, default="neutral")
    content = Column(Text, nullable=False)
    
    # Dialectic tracking: claim_id for this argument, and which prior claims it responds to
    claim_id = Column(String(50), nullable=True)  # e.g., "R1-PRO-PO-001"
    responds_to_claim_ids = Column(Text, nullable=True)  # JSON array of claim IDs this responds to
    concession = Column(Text, nullable=True)  # What this side concedes from opponent
    rebuttal = Column(Text, nullable=True)  # What this side rebuts
    revised_position = Column(Text, nullable=True)  # How position changed after considering opponent

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    debate_run = relationship("DebateRun", back_populates="arguments")


class OperatorDebateInput(Base):
    __tablename__ = "operator_debate_inputs"

    id = Column(Integer, primary_key=True, index=True)
    work_item_id = Column(
        Integer,
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # pro | con | neutral | auto_assign
    stance_requested = Column(String(20), nullable=False, default="auto_assign")
    # pro | con | neutral — populated when execution actually places the
    # argument on a side. NULL while the operator's request is still
    # auto_assign and no run has consumed it yet.
    stance_assigned = Column(String(20), nullable=True)
    content = Column(Text, nullable=False)
    considered_in_run_id = Column(
        Integer,
        ForeignKey("debate_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


class DebateExecutionConfig(Base):
    """SQL-backed configuration for debate execution bridge.

    Stored as a singleton row (id=1). UI manages all settings.
    Environment variables may seed defaults on first start only.

    Supports two modes:
    - single_model: All roles use default_model (default, recommended for most users)
    - role_models: Separate models for pro/con/arbiter roles (advanced)

    Model selection references ModelHost entries by ID.
    """
    __tablename__ = "debate_execution_config"

    id = Column(Integer, primary_key=True, index=True)
    # Always 1 for singleton pattern

    # Core settings
    enabled = Column(Boolean, nullable=False, default=False)
    provider = Column(String(50), nullable=False, default="openai_compatible")
    base_url = Column(String(500), nullable=False, default="http://ai-4080:11434/v1")

    # Model mode: single_model (default) or role_models (advanced)
    model_mode = Column(String(20), nullable=False, default="single_model")

    # Single-model mode: all roles use this model/host
    default_host_id = Column(Integer, nullable=True)
    default_model = Column(String(200), nullable=False, default="deepseek-r1:32b")

    # Role-specific models (only used when model_mode='role_models')
    # Pro/Builder model: Product Owner, UX/Design Reviewer, Technical Architect, Builder
    pro_host_id = Column(Integer, nullable=True)
    pro_model = Column(String(200), nullable=True)
    # Con/Skeptic model: Skeptic/Red Team, Security/Privacy Reviewer
    con_host_id = Column(Integer, nullable=True)
    con_model = Column(String(200), nullable=True)
    # Arbiter model: Final Arbiter
    arbiter_host_id = Column(Integer, nullable=True)
    arbiter_model = Column(String(200), nullable=True)
    # Optional fallback model (stored for future use)
    fallback_host_id = Column(Integer, nullable=True)
    fallback_model = Column(String(200), nullable=True)

    # Secret — stored server-side only, never returned to UI
    api_key = Column(String(500), nullable=True)

    # Timeouts and limits
    timeout_seconds = Column(Integer, nullable=False, default=180)
    max_output_chars = Column(Integer, nullable=False, default=12000)

    # Debate defaults
    default_rounds = Column(Integer, nullable=False, default=2)

    # Security guards
    allow_cloud_endpoints = Column(Boolean, nullable=False, default=False)

    # Model warmup settings
    warm_model_before_debate = Column(Boolean, nullable=False, default=True)
    warmup_timeout_seconds = Column(Integer, nullable=False, default=300)
    keep_model_loaded_for = Column(String(20), nullable=False, default="1h")
    fail_debate_if_warmup_fails = Column(Boolean, nullable=False, default=True)
    
    # Failed run display settings
    visible_failed_runs_limit = Column(Integer, nullable=False, default=2)
    
    # Worker configuration
    execution_backend = Column(String(20), nullable=False, default="worker")  # worker | sync
    worker_enabled = Column(Boolean, nullable=False, default=True)
    worker_poll_interval_seconds = Column(Integer, nullable=False, default=3)
    worker_lease_seconds = Column(Integer, nullable=False, default=300)
    worker_heartbeat_seconds = Column(Integer, nullable=False, default=10)
    worker_max_concurrent_runs = Column(Integer, nullable=False, default=1)
    turn_timeout_seconds = Column(Integer, nullable=True)  # Per-turn timeout (defaults to timeout_seconds)
    whole_run_timeout_seconds = Column(Integer, nullable=True)  # Whole-run max duration
    retry_failed_turn_enabled = Column(Boolean, nullable=False, default=True)
    max_turn_retries = Column(Integer, nullable=False, default=1)
    
    # Display settings
    display_timezone = Column(String(50), nullable=False, default="Australia/Sydney")  # IANA timezone name
    
    # Metadata
    notes = Column(Text, nullable=True)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ModelHost(Base):
    """SQL-backed model host configuration.

    Represents a model provider endpoint (Ollama native, OpenAI-compatible, xAI, etc.).
    UI manages hosts; debate execution references them.
    """
    __tablename__ = "model_hosts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    provider_type = Column(String(30), nullable=False, default="ollama_native")  # ollama_native, openai_compatible, xai, other
    provider = Column(String(50), nullable=False, default="openai_compatible")  # Legacy field, kept for compatibility
    base_url = Column(String(500), nullable=False)
    api_key = Column(String(500), nullable=True)
    enabled = Column(Boolean, nullable=False, default=True)
    allow_cloud_endpoints = Column(Boolean, nullable=False, default=False)

    # Capability flags
    supports_native_ollama = Column(Boolean, nullable=True)  # /api/tags, /api/chat
    supports_openai_chat_completions = Column(Boolean, nullable=True)  # /v1/chat/completions
    supports_model_list = Column(Boolean, nullable=True)
    supports_loaded_models = Column(Boolean, nullable=True)  # /api/ps
    preferred_generation_api = Column(String(30), nullable=True)  # ollama_native, openai_chat_completions

    # Test/refresh status
    last_test_status = Column(String(20), nullable=True)  # success, warning, failed, unknown
    last_test_message = Column(Text, nullable=True)
    last_tested_at = Column(DateTime, nullable=True)
    last_models_refresh_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    last_capability_result = Column(Text, nullable=True)  # JSON safe capability test result

    # Hermes sync metadata
    source = Column(String(20), nullable=False, default="manual")  # manual | hermes
    source_key = Column(String(200), nullable=True)  # Stable identifier from Hermes
    profile_name = Column(String(100), nullable=True)  # Hermes profile name
    provider_name = Column(String(100), nullable=True)  # Hermes provider name
    sync_enabled = Column(Boolean, nullable=False, default=True)
    last_synced_at = Column(DateTime, nullable=True)
    last_sync_status = Column(String(20), nullable=True)  # success, failed, skipped
    last_sync_error = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationship to model catalog
    models = relationship(
        "ModelHostModel",
        back_populates="host",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key)


class ModelHostModel(Base):
    """Model catalog entry for a ModelHost.

    Discovered via GET {base_url}/models endpoint.
    """
    __tablename__ = "model_host_models"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(
        Integer,
        ForeignKey("model_hosts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_id = Column(String(200), nullable=False, index=True)  # e.g., "deepseek-r1:32b"
    display_name = Column(String(200), nullable=True)  # Human-friendly name
    raw_json = Column(Text, nullable=True)  # Bounded raw metadata
    is_available = Column(Boolean, nullable=False, default=True)
    discovered_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Hermes sync metadata
    source = Column(String(20), nullable=False, default="manual")  # manual | hermes
    source_key = Column(String(200), nullable=True)
    last_synced_at = Column(DateTime, nullable=True)

    host = relationship("ModelHost", back_populates="models")


class ModelSyncRun(Base):
    """Record of a Hermes model sync run."""
    __tablename__ = "model_sync_runs"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(20), nullable=False, default="hermes")
    status = Column(String(20), nullable=False, default="running")  # running, completed, failed
    hosts_discovered = Column(Integer, nullable=True)
    models_discovered = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)  # Safe/redacted error message
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)


class RepoLock(Base):
    """Active or historical lock held by a builder run on a target repo.

    The lock is keyed by ``repo_path`` which is unique — only one active lock
    can exist per repo at a time. When ``lock_status`` is "active" the row
    represents an in-flight build; ``release_repo_lock`` transitions it to
    "released" (or "failed"/"aborted") and stamps ``released_at``.
    """

    __tablename__ = "repo_locks"

    id = Column(Integer, primary_key=True, index=True)
    repo_path = Column(String(500), nullable=False, unique=True)
    repo_name = Column(String(100), nullable=False)
    work_item_id = Column(Integer, nullable=True)
    task_id = Column(String(100), nullable=True)
    branch_name = Column(String(100), nullable=False)
    commit_sha = Column(String(40), nullable=False)
    lock_owner = Column(String(100), nullable=True)
    lock_status = Column(String(20), nullable=False, default="active")
    started_at = Column(DateTime, server_default=func.now(), nullable=False)
    released_at = Column(DateTime, nullable=True)
    release_reason = Column(String(200), nullable=True)


class AppActionLog(Base):
    __tablename__ = "app_action_logs"

    id = Column(Integer, primary_key=True, index=True)
    app_id = Column(
        Integer,
        ForeignKey("apps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action = Column(String(20), nullable=False)
    result = Column(String(20), nullable=False)
    exit_code = Column(Integer, nullable=True)
    stdout_tail = Column(Text, nullable=True)
    stderr_tail = Column(Text, nullable=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    # Phase tracking for progress visibility during long-running actions.
    # Phases: queued, validating_docker, running_compose, collecting_output, completed, failed, timed_out
    phase = Column(String(30), nullable=False, default="queued")
    # Elapsed time in seconds (updated while running, final value on completion)
    elapsed_seconds = Column(Integer, nullable=True)

    app = relationship("App", back_populates="action_logs")


# BuilderTask is defined in models_builder.py - import after all Base classes are defined
from .models_builder import BuilderTask

# Now backfill the relationship on WorkItem (already defined above)
# The relationship string reference works because BuilderTask is now imported
