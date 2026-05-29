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
