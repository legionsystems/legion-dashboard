"""Hermes builder status projection to Work Item status.

This module provides reusable status synchronization between Hermes Kanban
builder tasks and LEGION Dashboard work items. It ensures that when a Hermes
builder task changes status (e.g., running → done), the corresponding work
item status is projected accordingly (e.g., approved → implemented).

Status Mapping
--------------
Hermes Status      →  Work Item Status
─────────────────────────────────────────
triage             →  approved (awaiting builder start)
todo               →  approved (queued for builder)
ready              →  approved (ready to build)
scheduled          →  approved (scheduled for build)
running            →  building (implementation in progress)
review             →  review (implementation complete, awaiting review)
done               →  implemented (fully complete)
blocked            →  blocked (blocked during build)
archived           →  archived (archived)

Usage
-----
# Sync a single work item
from .builder_status_projection import sync_work_item_status_from_builder
sync_work_item_status_from_builder(db, work_item_id=12)

# Sync all work items with valid builder tasks
from .builder_status_projection import sync_all_builder_status_projections
sync_all_builder_status_projections(db)

# Use in API endpoint
@router.post("/tasks/{task_id}/sync")
def sync_builder_task(task_id: int, db: Session):
    ...
    # After syncing builder task, project status to work item
    sync_work_item_status_from_builder(db, builder_task.work_item_id)
"""
from datetime import datetime
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from .models import BuilderTask, WorkItem

# Hermes status → Work Item status projection map
HERMES_TO_WORK_ITEM_STATUS: Dict[str, str] = {
    "triage": "approved",      # Awaiting builder start
    "todo": "approved",        # Queued for builder
    "ready": "approved",       # Ready to build
    "scheduled": "approved",   # Scheduled for build
    "running": "building",     # Implementation in progress
    "review": "review",        # Implementation complete, awaiting review
    "done": "implemented",     # Fully complete
    "blocked": "blocked",      # Blocked during build
    "archived": "archived",    # Archived
}

# Work Item statuses that indicate build lifecycle
BUILD_LIFECYCLE_STATUSES = frozenset({
    "building",
    "review",
    "implemented",
    "blocked",
    "archived",
})


def hermes_status_to_work_item_status(hermes_status: str) -> Optional[str]:
    """Convert Hermes builder task status to Work Item status.
    
    Args:
        hermes_status: Status from Hermes Kanban (e.g., "running", "done")
    
    Returns:
        Corresponding Work Item status, or None if mapping not found
    """
    return HERMES_TO_WORK_ITEM_STATUS.get(hermes_status.lower())


def sync_work_item_status_from_builder(
    db: Session,
    work_item_id: int,
    hermes_status: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Project Hermes builder task status to Work Item status.
    
    This is the core status projection function. It updates the work item
    status based on the Hermes builder task status, but ONLY if the status
    has actually changed (to avoid unnecessary DB writes).
    
    Args:
        db: Database session
        work_item_id: ID of the work item to update
        hermes_status: Optional Hermes status override. If not provided,
                       queries the latest valid builder_task for this work item.
    
    Returns:
        Tuple of (changed: bool, old_status: str|None, new_status: str|None)
        - changed: True if work item status was updated
        - old_status: Previous work item status (None if not found)
        - new_status: New work item status (None if not updated)
    """
    work_item = db.query(WorkItem).filter(WorkItem.id == work_item_id).first()
    if not work_item:
        return (False, None, None)
    
    old_status: str = str(work_item.status)
    
    # Determine Hermes status to project
    if hermes_status is None:
        # Query latest valid builder task for this work item
        builder_task = db.query(BuilderTask).filter(
            BuilderTask.work_item_id == work_item_id,
            BuilderTask.is_valid != False,  # True or NULL
        ).order_by(BuilderTask.created_at.desc()).first()
        
        if not builder_task:
            # No valid builder task, cannot project status
            return (False, old_status, None)
        
        hermes_status = str(builder_task.hermes_status)
    
    # Map Hermes status to Work Item status
    projected_status = hermes_status_to_work_item_status(hermes_status)
    
    if not projected_status:
        # Unknown Hermes status, cannot project
        return (False, old_status, None)
    
    # Only update if status actually changed
    if old_status != projected_status:
        work_item.status = projected_status
        work_item.updated_at = datetime.utcnow()
        db.commit()
        return (True, old_status, projected_status)
    
    # No change needed
    return (False, old_status, projected_status)


def sync_all_builder_status_projections(
    db: Session,
    limit: int = 100,
) -> Dict[str, int]:
    """Sync status projection for all work items with valid builder tasks.
    
    This is a batch operation suitable for cron jobs or manual sync runs.
    It projects Hermes builder task statuses to all linked work items.
    
    Args:
        db: Database session
        limit: Maximum number of work items to process (default 100)
    
    Returns:
        Dict with sync statistics:
        - processed: Number of work items checked
        - updated: Number of work items with status changed
        - unchanged: Number of work items already in sync
        - errors: Number of errors encountered
    """
    stats = {
        "processed": 0,
        "updated": 0,
        "unchanged": 0,
        "errors": 0,
    }
    
    # Get all work items with valid builder tasks
    builder_tasks = db.query(BuilderTask).filter(
        BuilderTask.is_valid != False,  # True or NULL
    ).limit(limit).all()
    
    # Deduplicate by work_item_id (keep latest)
    seen_work_item_ids = set()
    unique_tasks = []
    for bt in builder_tasks:
        if bt.work_item_id not in seen_work_item_ids:
            seen_work_item_ids.add(bt.work_item_id)
            unique_tasks.append(bt)
    
    for builder_task in unique_tasks:
        stats["processed"] += 1
        try:
            changed, _, _ = sync_work_item_status_from_builder(
                db,
                builder_task.work_item_id,
                builder_task.hermes_status,
            )
            if changed:
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
        except Exception:
            stats["errors"] += 1
    
    return stats


def is_build_lifecycle_status(status: str) -> bool:
    """Check if a status is part of the build lifecycle.
    
    Build lifecycle statuses are those that indicate the work item
    has entered the builder workflow (as opposed to pre-build statuses
    like draft, debated, approved).
    
    Args:
        status: Work Item status string
    
    Returns:
        True if status is a build lifecycle status
    """
    return status.lower() in BUILD_LIFECYCLE_STATUSES
