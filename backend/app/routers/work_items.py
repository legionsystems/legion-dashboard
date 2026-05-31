from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..debate import (
    attach_operator_inputs_to_run,
    execution_bridge_configured,
    list_pending_operator_inputs,
    queue_debate_for_eligible_item,
    queue_debate_run,
)
from ..debate_executor import (
    ExecutionConfig,
    execute_debate_run,
    get_execution_config,
)
from ..models import (
    DebateArgument,
    DebateRun,
    FollowUp,
    OperatorDebateInput,
    WorkItem,
)
from ..schemas import (
    BlockRequest,
    BulkArchiveRequest,
    BulkArchiveTestItemsRequest,
    DebateRunBulkHideRequest,
    DebateRunCreate,
    DebateRunDetail,
    DebateRunHideRequest,
    DebateRunHideResponse,
    DebateRunSummary,
    FollowUpCreate,
    FollowUpResponse,
    OperatorDebateInputCreate,
    OperatorDebateInputResponse,
    WorkItemArchiveRequest,
    WorkItemClassificationUpdate,
    WorkItemCreate,
    WorkItemResponse,
    WorkItemUpdate,
)

router = APIRouter(prefix="/api/work-items", tags=["work-items"])


def _get_or_404(db: Session, work_item_id: int) -> WorkItem:
    item = db.query(WorkItem).filter(WorkItem.id == work_item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found")
    return item


def _latest_debate_for(db: Session, work_item_id: int) -> Optional[DebateRun]:
    return (
        db.query(DebateRun)
        .filter(DebateRun.work_item_id == work_item_id)
        .order_by(DebateRun.id.desc())
        .first()
    )


def _serialize_with_debate(db: Session, item: WorkItem) -> WorkItemResponse:
    response = WorkItemResponse.model_validate(item)
    latest = _latest_debate_for(db, item.id)
    if latest is not None:
        response.latest_debate = DebateRunSummary.model_validate(latest)
    return response


def _serialize_many_with_debate(
    db: Session, items: List[WorkItem]
) -> List[WorkItemResponse]:
    if not items:
        return []
    ids = [it.id for it in items]
    # Pull the max debate id per work item in a single query, then fetch
    # those rows. Keeps the list endpoint at O(2) queries instead of O(N).
    from sqlalchemy import func as sa_func

    subq = (
        db.query(
            DebateRun.work_item_id,
            sa_func.max(DebateRun.id).label("max_id"),
        )
        .filter(DebateRun.work_item_id.in_(ids))
        .group_by(DebateRun.work_item_id)
        .subquery()
    )
    runs = (
        db.query(DebateRun)
        .join(subq, DebateRun.id == subq.c.max_id)
        .all()
    )
    by_work_item = {r.work_item_id: r for r in runs}
    out: List[WorkItemResponse] = []
    for it in items:
        resp = WorkItemResponse.model_validate(it)
        run = by_work_item.get(it.id)
        if run is not None:
            resp.latest_debate = DebateRunSummary.model_validate(run)
        out.append(resp)
    return out


@router.get("", response_model=List[WorkItemResponse])
def list_work_items(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    view: str = Query("active", description="active|archived|all"),
    generated: str = Query("human", description="human|system|test|all"),
    db: Session = Depends(get_db),
):
    query = db.query(WorkItem)
    
    # Archive view filter
    if view == "active":
        query = query.filter(WorkItem.archived == False)
    elif view == "archived":
        query = query.filter(WorkItem.archived == True)
    # view == "all" includes both
    
    # Generated/test filter
    if generated == "human":
        query = query.filter(WorkItem.is_system_generated == False, WorkItem.is_test_item == False)
    elif generated == "system":
        query = query.filter(WorkItem.is_system_generated == True)
    elif generated == "test":
        query = query.filter(WorkItem.is_test_item == True)
    # generated == "all" includes everything
    
    if type is not None:
        query = query.filter(WorkItem.type == type)
    if status is not None:
        query = query.filter(WorkItem.status == status)
    items = query.order_by(WorkItem.id.desc()).all()
    return _serialize_many_with_debate(db, items)


@router.post("", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
def create_work_item(payload: WorkItemCreate, db: Session = Depends(get_db)):
    item = WorkItem(**payload.model_dump(exclude_unset=True))
    db.add(item)
    db.commit()
    db.refresh(item)
    # Debate auto-queue is advisory and must not block work item creation.
    # If queueing fails for any reason, the work item still exists and the
    # operator can rerun manually from the detail page.
    try:
        queue_debate_for_eligible_item(db, item)
        db.commit()
    except Exception:  # pragma: no cover - defensive
        db.rollback()
    return _serialize_with_debate(db, item)


@router.get("/{work_item_id}", response_model=WorkItemResponse)
def get_work_item(work_item_id: int, db: Session = Depends(get_db)):
    return _serialize_with_debate(db, _get_or_404(db, work_item_id))


@router.put("/{work_item_id}", response_model=WorkItemResponse)
def update_work_item(
    work_item_id: int,
    payload: WorkItemUpdate,
    db: Session = Depends(get_db),
):
    item = _get_or_404(db, work_item_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    # Always re-evaluate. The helper handles both the "not debate-eligible"
    # case (no-op) and snapshot de-duplication (no duplicate runs for an
    # unchanged item).
    try:
        queue_debate_for_eligible_item(db, item)
        db.commit()
    except Exception:  # pragma: no cover - defensive
        db.rollback()
    return _serialize_with_debate(db, item)


@router.post("/{work_item_id}/approve", response_model=WorkItemResponse)
def approve_work_item(work_item_id: int, db: Session = Depends(get_db)):
    item = _get_or_404(db, work_item_id)
    item.approved_by_operator = True
    item.approval_timestamp = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return _serialize_with_debate(db, item)


@router.post("/{work_item_id}/block", response_model=WorkItemResponse)
def block_work_item(
    work_item_id: int,
    payload: Optional[BlockRequest] = None,
    db: Session = Depends(get_db),
):
    item = _get_or_404(db, work_item_id)
    item.status = "blocked"
    if payload and payload.override_reason is not None:
        item.override_reason = payload.override_reason
        item.override_timestamp = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return _serialize_with_debate(db, item)


@router.get("/{work_item_id}/follow-ups", response_model=List[FollowUpResponse])
def list_follow_ups(work_item_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, work_item_id)
    return (
        db.query(FollowUp)
        .filter(FollowUp.work_item_id == work_item_id)
        .order_by(FollowUp.id.desc())
        .all()
    )


@router.post(
    "/{work_item_id}/follow-ups",
    response_model=FollowUpResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_follow_up(
    work_item_id: int,
    payload: FollowUpCreate,
    db: Session = Depends(get_db),
):
    _get_or_404(db, work_item_id)
    follow_up = FollowUp(work_item_id=work_item_id, **payload.model_dump(exclude_unset=True))
    db.add(follow_up)
    db.commit()
    db.refresh(follow_up)
    return follow_up


# ---------------------------------------------------------------------------
# Debate endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{work_item_id}/debates",
    response_model=DebateRunDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_debate_run(
    work_item_id: int,
    payload: DebateRunCreate,
    db: Session = Depends(get_db),
):
    item = _get_or_404(db, work_item_id)
    # Manual reruns always succeed (force=True): the operator gets a fresh
    # DebateRun row, prior runs are preserved untouched.
    run = queue_debate_run(
        db,
        item,
        trigger=payload.trigger,
        rounds=payload.rounds,
        force=True,
    )
    if run is None:  # pragma: no cover - force=True never returns None
        raise HTTPException(status_code=500, detail="Failed to queue debate")

    # Attach any pending operator inputs for context — the operator
    # explicitly asked for a new run, so consume what's waiting.
    pending = list_pending_operator_inputs(db, work_item_id)
    if pending:
        attach_operator_inputs_to_run(db, run, pending)

    db.commit()
    db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Debate execution endpoints
# ---------------------------------------------------------------------------
# IMPORTANT: /next must come BEFORE /{run_id}/execute to avoid "next" being
# matched as a run_id. FastAPI matches routes in definition order.


@router.get(
    "/{work_item_id}/debates/next",
    response_model=DebateRunDetail,
)
def execute_next_debate(
    work_item_id: int,
    db: Session = Depends(get_db),
):
    """Execute the latest queued debate run for a work item.

    Creates a new run if none exists (manual trigger).
    """
    item = _get_or_404(db, work_item_id)
    # Get latest QUEUED or FAILED run, not any run
    run = (
        db.query(DebateRun)
        .filter(
            DebateRun.work_item_id == work_item_id,
            DebateRun.status.in_(["queued", "failed"]),
        )
        .order_by(DebateRun.id.desc())
        .first()
    )

    if run is None:
        # No existing run to execute — create one
        run = queue_debate_run(db, item, trigger="operator_requested", rounds=2, force=True)
        pending = list_pending_operator_inputs(db, work_item_id)
        if pending:
            attach_operator_inputs_to_run(db, run, pending)
        db.commit()
        db.refresh(run)

    # Execute if queued or failed
    if run.status in ("queued", "failed"):
        config = get_execution_config(db)
        if not config.enabled:
            run.error_message = (
                "Debate execution is disabled in Settings. "
                "Enable it at Settings > Debate Execution."
            )
            db.commit()
            db.refresh(run)
            return run

        execute_debate_run(db, run, item, config)
        db.commit()
        db.refresh(run)

    return run


@router.get(
    "/{work_item_id}/debates/{run_id}",
    response_model=DebateRunDetail,
)
def get_debate_run(
    work_item_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    _get_or_404(db, work_item_id)
    run = (
        db.query(DebateRun)
        .filter(DebateRun.id == run_id, DebateRun.work_item_id == work_item_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Debate run not found")
    return run


@router.post(
    "/{work_item_id}/debates/{run_id}/execute",
    response_model=DebateRunDetail,
)
async def execute_debate(
    work_item_id: int,
    run_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Execute a queued or failed debate run.

    If execution bridge is not configured, returns the run with status='queued'
    and a clear error message.

    If already running, returns current status.
    If completed, returns existing result (does not re-execute).
    
    Execution happens in background - returns immediately with status='running'.
    """
    item = _get_or_404(db, work_item_id)
    run = (
        db.query(DebateRun)
        .filter(DebateRun.id == run_id, DebateRun.work_item_id == work_item_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Debate run not found")

    # Already completed — do not overwrite
    if run.status == "completed":
        return run

    # Check if execution bridge is configured
    config = get_execution_config(db)
    if not config.enabled:
        # Return run with clear message
        if not run.error_message:
            run.error_message = (
                "Debate execution is disabled in Settings. "
                "Enable it at Settings > Debate Execution."
            )
        db.commit()
        db.refresh(run)
        return run

    # Already running — return current status
    if run.status == "running":
        return run

    # Mark as running immediately so UI shows progress
    run.status = "running"
    run.model_route = f"{config.provider}:{config.model}"
    run.provenance = f"executed via {config.redacted_base_url()}"
    db.commit()
    
    # Execute in background - pass only IDs and config values, not the session
    background_tasks.add_task(_execute_debate_bg, run.id, item.id, config.provider, config.base_url, config.model, config.api_key, config.timeout_seconds, config.max_output_chars)
    
    db.refresh(run)
    return run


def _execute_debate_bg(run_id: int, work_item_id: int, provider: str, base_url: str, model: str, api_key: str, timeout_seconds: int, max_output_chars: int):
    """Background task to execute debate run."""
    from app.database import SessionLocal
    from app.debate_executor import ExecutionConfig
    from app.models import DebateRun, WorkItem
    
    # Fresh session for background task
    db_session = SessionLocal()
    try:
        run = db_session.query(DebateRun).filter(DebateRun.id == run_id).first()
        item = db_session.query(WorkItem).filter(WorkItem.id == work_item_id).first()
        
        if not run or not item:
            return
        
        config = ExecutionConfig(
            enabled=True,
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        
        execute_debate_run(db_session, run, item, config)
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        # Mark run as failed
        run = db_session.query(DebateRun).filter(DebateRun.id == run_id).first()
        if run:
            run.status = "failed"
            run.error_message = f"Background execution error: {type(e).__name__}: {str(e)[:500]}"
            run.completed_at = datetime.utcnow()
            db_session.commit()
    finally:
        db_session.close()


@router.get(
    "/{work_item_id}/debate-inputs",
    response_model=List[OperatorDebateInputResponse],
)
def list_debate_inputs(work_item_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, work_item_id)
    return (
        db.query(OperatorDebateInput)
        .filter(OperatorDebateInput.work_item_id == work_item_id)
        .order_by(OperatorDebateInput.id.desc())
        .all()
    )


# ---------------------------------------------------------------------------
# Debate run cleanup/visibility endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{work_item_id}/debates/{run_id}/hide",
    response_model=DebateRunHideResponse,
)
def hide_debate_run(
    work_item_id: int,
    run_id: int,
    payload: Optional[DebateRunHideRequest] = None,
    db: Session = Depends(get_db),
):
    """Hide a debate run from default view.
    
    The run remains in the database and can be restored later.
    Hidden runs are excluded from default list views.
    """
    _get_or_404(db, work_item_id)
    run = db.query(DebateRun).filter(DebateRun.id == run_id, DebateRun.work_item_id == work_item_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Debate run not found")
    
    if run.hidden_at:
        raise HTTPException(status_code=400, detail="Debate run is already hidden")
    
    run.hidden_at = datetime.utcnow()
    run.hidden_by = "operator"  # Could be extended to track actual user
    if payload:
        run.hidden_reason = payload.reason
        run.hidden_category = payload.category
    else:
        run.hidden_category = "operator_cleanup"
    
    db.commit()
    db.refresh(run)
    
    return DebateRunHideResponse(
        id=run.id,
        work_item_id=run.work_item_id,
        status=run.status,
        hidden_at=run.hidden_at,
        hidden_by=run.hidden_by,
        hidden_reason=run.hidden_reason,
        hidden_category=run.hidden_category,
    )


@router.post(
    "/{work_item_id}/debates/{run_id}/restore",
    response_model=DebateRunHideResponse,
)
def restore_debate_run(
    work_item_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    """Restore a hidden debate run to default view."""
    _get_or_404(db, work_item_id)
    run = db.query(DebateRun).filter(DebateRun.id == run_id, DebateRun.work_item_id == work_item_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Debate run not found")
    
    if not run.hidden_at:
        raise HTTPException(status_code=400, detail="Debate run is not hidden")
    
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


@router.post(
    "/{work_item_id}/debates/bulk/hide-failed",
    response_model=dict,
)
def bulk_hide_failed_debates(
    work_item_id: int,
    payload: Optional[DebateRunBulkHideRequest] = None,
    db: Session = Depends(get_db),
):
    """Bulk hide failed debate runs for a work item.
    
    By default, keeps the latest failed run visible for reference.
    Use older_than_run_id to hide only runs older than a specific run.
    """
    _get_or_404(db, work_item_id)
    
    # Get all failed runs for this work item
    query = db.query(DebateRun).filter(
        DebateRun.work_item_id == work_item_id,
        DebateRun.status == "failed",
        DebateRun.hidden_at.is_(None),  # Only hide non-hidden runs
    )
    
    if payload and payload.older_than_run_id:
        query = query.filter(DebateRun.id < payload.older_than_run_id)
    
    failed_runs = query.order_by(DebateRun.id.desc()).all()
    
    if not failed_runs:
        return {
            "hidden_count": 0,
            "message": "No failed runs to hide",
        }
    
    # Keep latest failed run if requested
    keep_latest = payload.keep_latest_failed if payload else True
    if keep_latest and len(failed_runs) > 1:
        failed_runs = failed_runs[1:]  # Skip the most recent
    
    if not failed_runs:
        return {
            "hidden_count": 0,
            "message": "No runs to hide after keeping latest",
        }
    
    # Hide them
    reason = payload.reason if payload and payload.reason else "Bulk hide of failed attempts"
    for run in failed_runs:
        run.hidden_at = datetime.utcnow()
        run.hidden_by = "operator"
        run.hidden_reason = reason
        run.hidden_category = "repeated_timeout" if "timeout" in (run.error_message or "").lower() else "setup_failure"
    
    db.commit()
    
    return {
        "hidden_count": len(failed_runs),
        "hidden_run_ids": [r.id for r in failed_runs],
        "message": f"Hidden {len(failed_runs)} failed debate runs",
    }


@router.get(
    "/{work_item_id}/debates",
    response_model=List[DebateRunSummary],
)
def list_debate_runs(
    work_item_id: int,
    view: str = Query("active", description="active|hidden|all"),
    status: str = Query("all", description="queued|running|completed|failed|all"),
    db: Session = Depends(get_db),
):
    """List debate runs for a work item with visibility filtering.
    
    view=active (default): Shows non-hidden runs
    view=hidden: Shows only hidden runs
    view=all: Shows all runs regardless of hidden status
    
    status filters by run status (all by default)
    """
    _get_or_404(db, work_item_id)
    
    query = db.query(DebateRun).filter(DebateRun.work_item_id == work_item_id)
    
    # Apply visibility filter
    if view == "active":
        query = query.filter(DebateRun.hidden_at.is_(None))
    elif view == "hidden":
        query = query.filter(DebateRun.hidden_at.is_not(None))
    # view == "all" includes everything
    
    # Apply status filter
    if status != "all":
        query = query.filter(DebateRun.status == status)
    
    return query.order_by(DebateRun.id.desc()).all()


@router.post(
    "/{work_item_id}/debate-inputs",
    response_model=OperatorDebateInputResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_debate_input(
    work_item_id: int,
    payload: OperatorDebateInputCreate,
    db: Session = Depends(get_db),
):
    _get_or_404(db, work_item_id)
    op_input = OperatorDebateInput(
        work_item_id=work_item_id,
        content=payload.content.strip(),
        stance_requested=payload.stance_requested,
    )
    db.add(op_input)
    db.commit()
    db.refresh(op_input)
    return op_input


# ---------------------------------------------------------------------------
# Bulk archive endpoints (must come BEFORE /{work_item_id} routes)
# ---------------------------------------------------------------------------


@router.post(
    "/bulk/archive",
    response_model=List[WorkItemResponse],
)
def bulk_archive_work_items(
    payload: BulkArchiveRequest,
    db: Session = Depends(get_db),
):
    """Bulk archive multiple work items."""
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No work item IDs provided")
    
    items = db.query(WorkItem).filter(WorkItem.id.in_(payload.ids)).all()
    if len(items) != len(payload.ids):
        raise HTTPException(status_code=404, detail="Some work items not found")
    
    for item in items:
        if not item.archived:
            item.archived = True
            item.archived_at = datetime.utcnow()
            if payload.reason:
                item.archive_reason = payload.reason
    
    db.commit()
    
    # Refresh all items
    for item in items:
        db.refresh(item)
    
    return [_serialize_with_debate(db, item) for item in items]


@router.post(
    "/bulk/archive-test-items",
    response_model=dict,
)
def bulk_archive_test_items(
    payload: BulkArchiveTestItemsRequest = None,
    db: Session = Depends(get_db),
):
    """Bulk archive test/system-generated work items. Dry run by default."""
    dry_run = payload.dry_run if payload else True
    older_than_days = payload.older_than_days if payload else None
    
    query = db.query(WorkItem).filter(
        WorkItem.is_test_item == True,
        WorkItem.archived == False,
    )
    
    if older_than_days:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        query = query.filter(WorkItem.created_at < cutoff)
    
    items_to_archive = query.all()
    
    if dry_run:
        return {
            "dry_run": True,
            "would_archive_count": len(items_to_archive),
            "would_archive_ids": [item.id for item in items_to_archive],
        }
    
    # Actually archive
    for item in items_to_archive:
        item.archived = True
        item.archived_at = datetime.utcnow()
        item.archive_reason = "Bulk archive of test items"
    
    db.commit()
    
    return {
        "dry_run": False,
        "archived_count": len(items_to_archive),
        "archived_ids": [item.id for item in items_to_archive],
    }


# ---------------------------------------------------------------------------
# Archive lifecycle endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{work_item_id}/archive",
    response_model=WorkItemResponse,
)
def archive_work_item(
    work_item_id: int,
    payload: Optional[WorkItemArchiveRequest] = None,
    db: Session = Depends(get_db),
):
    """Archive a work item. Soft delete - preserves all history."""
    item = _get_or_404(db, work_item_id)
    if item.archived:
        raise HTTPException(status_code=400, detail="Work item is already archived")
    
    item.archived = True
    item.archived_at = datetime.utcnow()
    if payload and payload.reason:
        item.archive_reason = payload.reason
    
    db.commit()
    db.refresh(item)
    return _serialize_with_debate(db, item)


@router.post(
    "/{work_item_id}/restore",
    response_model=WorkItemResponse,
)
def restore_work_item(
    work_item_id: int,
    db: Session = Depends(get_db),
):
    """Restore an archived work item."""
    item = _get_or_404(db, work_item_id)
    if not item.archived:
        raise HTTPException(status_code=400, detail="Work item is not archived")
    
    item.archived = False
    item.archived_at = None
    item.archived_by = None
    item.archive_reason = None
    
    db.commit()
    db.refresh(item)
    return _serialize_with_debate(db, item)


@router.patch(
    "/{work_item_id}/classification",
    response_model=WorkItemResponse,
)
def update_work_item_classification(
    work_item_id: int,
    payload: WorkItemClassificationUpdate,
    db: Session = Depends(get_db),
):
    """Update work item classification (system-generated/test flags)."""
    item = _get_or_404(db, work_item_id)
    
    if payload.is_system_generated is not None:
        item.is_system_generated = payload.is_system_generated
    if payload.is_test_item is not None:
        item.is_test_item = payload.is_test_item
    if payload.tags is not None:
        item.tags = payload.tags
    
    db.commit()
    db.refresh(item)
    return _serialize_with_debate(db, item)
