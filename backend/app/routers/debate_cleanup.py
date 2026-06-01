"""Debate run cleanup and visibility control endpoints."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DebateRun, WorkItem
from ..schemas import (
    DebateRunBulkHideRequest,
    DebateRunHideRequest,
    DebateRunHideResponse,
    DebateRunSummary,
)

router = APIRouter(prefix="/api/work-items/{work_item_id}/debates", tags=["debates"])


def _get_work_item_or_404(db: Session, work_item_id: int) -> WorkItem:
    item = db.query(WorkItem).filter(WorkItem.id == work_item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found")
    return item


def _get_debate_run_or_404(db: Session, run_id: int) -> DebateRun:
    run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Debate run not found")
    return run


@router.get("", response_model=List[DebateRunSummary])
def list_debate_runs(
    work_item_id: int,
    view: str = Query(default="active", description="active|hidden|all"),
    status_filter: Optional[str] = Query(default=None, alias="status", description="Filter by status"),
    db: Session = Depends(get_db),
):
    """List debate runs for a work item with visibility filtering.
    
    Args:
        work_item_id: Work item ID
        view: 'active' (default, shows non-hidden), 'hidden' (only hidden), 'all' (both)
        status_filter: Optional status filter (queued|running|completed|failed)
    """
    _get_work_item_or_404(db, work_item_id)
    
    query = db.query(DebateRun).filter(DebateRun.work_item_id == work_item_id)
    
    # Apply visibility filter
    if view == "active":
        query = query.filter(DebateRun.hidden_at.is_(None))
    elif view == "hidden":
        query = query.filter(DebateRun.hidden_at.is_not(None))
    # 'all' shows everything
    
    # Apply status filter
    if status_filter and status_filter != "all":
        query = query.filter(DebateRun.status == status_filter)
    
    # Order by most recent first
    query = query.order_by(DebateRun.id.desc())
    
    runs = query.all()
    return [DebateRunSummary.model_validate(run) for run in runs]


@router.post("/{run_id}/hide", response_model=DebateRunHideResponse)
def hide_debate_run(
    work_item_id: int,
    run_id: int,
    payload: DebateRunHideRequest,
    db: Session = Depends(get_db),
):
    """Hide a debate run from default view.
    
    Does not delete the run - just marks it hidden for UI cleanliness.
    Hidden runs remain auditable and can be restored.
    """
    work_item = _get_work_item_or_404(db, work_item_id)
    run = _get_debate_run_or_404(db, run_id)
    
    # Verify run belongs to work item
    if run.work_item_id != work_item_id:
        raise HTTPException(status_code=400, detail="Debate run does not belong to this work item")
    
    # Mark as hidden
    run.hidden_at = datetime.utcnow()
    run.hidden_reason = payload.reason
    run.hidden_category = payload.category
    
    db.commit()
    db.refresh(run)
    
    return DebateRunHideResponse(
        id=run.id,
        work_item_id=run.work_item_id,
        status=run.status,
        hidden_at=run.hidden_at,
        hidden_by="operator",  # Could be extended to track actual user
        hidden_reason=run.hidden_reason,
        hidden_category=run.hidden_category,
    )


@router.post("/{run_id}/restore", response_model=DebateRunHideResponse)
def restore_debate_run(
    work_item_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    """Restore a hidden debate run to default view."""
    work_item = _get_work_item_or_404(db, work_item_id)
    run = _get_debate_run_or_404(db, run_id)
    
    # Verify run belongs to work item
    if run.work_item_id != work_item_id:
        raise HTTPException(status_code=400, detail="Debate run does not belong to this work item")
    
    if not run.hidden_at:
        raise HTTPException(status_code=400, detail="Run is not hidden")
    
    # Clear hidden fields
    run.hidden_at = None
    run.hidden_by = None
    run.hidden_reason = None
    run.hidden_category = None
    
    db.commit()
    db.refresh(run)
    
    return DebateRunHideResponse(
        id=run.id,
        work_item_id=run.work_item_id,
        status=run.status,
        hidden_at=None,
        hidden_by=None,
        hidden_reason=None,
        hidden_category=None,
    )


@router.post("/bulk/hide-failed", response_model=List[DebateRunHideResponse])
def bulk_hide_failed_runs(
    work_item_id: int,
    payload: DebateRunBulkHideRequest,
    db: Session = Depends(get_db),
):
    """Bulk hide failed debate runs for a work item.
    
    Args:
        work_item_id: Work item ID
        payload: Bulk hide request with optional older_than_run_id and keep_latest_failed
    """
    work_item = _get_work_item_or_404(db, work_item_id)
    
    # Build query for failed runs
    query = db.query(DebateRun).filter(
        DebateRun.work_item_id == work_item_id,
        DebateRun.status == "failed",
        DebateRun.hidden_at.is_(None),  # Only hide currently visible runs
    )
    
    # Apply older_than filter if specified
    if payload.older_than_run_id:
        query = query.filter(DebateRun.id < payload.older_than_run_id)
    
    # Get all matching runs
    all_failed = query.order_by(DebateRun.id.desc()).all()
    
    # Keep latest failed if requested
    if payload.keep_latest_failed and all_failed:
        all_failed = all_failed[1:]  # Skip the most recent failed run
    
    # Hide them
    results = []
    for run in all_failed:
        run.hidden_at = datetime.utcnow()
        run.hidden_reason = payload.reason or "Bulk hide failed runs"
        run.hidden_category = "repeated_timeout" if "timeout" in (run.error_type or "").lower() else "setup_failure"
        results.append(DebateRunHideResponse(
            id=run.id,
            work_item_id=run.work_item_id,
            status=run.status,
            hidden_at=run.hidden_at,
            hidden_by="operator",
            hidden_reason=run.hidden_reason,
            hidden_category=run.hidden_category,
        ))
    
    db.commit()
    return results


# Note: Individual debate run detail is served by work_items router
# at /api/work-items/{work_item_id}/debates/{run_id} with DebateRunDetail response
