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

    app = relationship("App", back_populates="action_logs")
