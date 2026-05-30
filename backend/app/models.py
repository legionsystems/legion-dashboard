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

    # queued | running | completed | failed
    status = Column(String(20), nullable=False, default="queued")

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
    role = Column(String(60), nullable=False)
    # pro | con | neutral | arbiter
    side = Column(String(20), nullable=False, default="neutral")
    content = Column(Text, nullable=False)

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
    """
    __tablename__ = "debate_execution_config"

    id = Column(Integer, primary_key=True, index=True)
    # Always 1 for singleton pattern

    # Core settings
    enabled = Column(Boolean, nullable=False, default=False)
    provider = Column(String(50), nullable=False, default="openai_compatible")
    base_url = Column(String(500), nullable=False, default="http://ai-4080:11434/v1")
    model = Column(String(200), nullable=False, default="deepseek-r1:32b")

    # Secret — stored server-side only, never returned to UI
    api_key_encrypted = Column(Text, nullable=True)
    # For now, store as plaintext with clear security TODO
    # TODO: Implement proper encryption-at-rest using SecretStorage or similar
    api_key = Column(String(500), nullable=True)

    # Timeouts and limits
    timeout_seconds = Column(Integer, nullable=False, default=180)
    max_output_chars = Column(Integer, nullable=False, default=12000)

    # Debate defaults
    default_rounds = Column(Integer, nullable=False, default=2)

    # Security guards
    allow_cloud_endpoints = Column(Boolean, nullable=False, default=False)

    # Metadata
    notes = Column(Text, nullable=True)
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


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
