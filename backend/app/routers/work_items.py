from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    DebateRunCreate,
    DebateRunDetail,
    DebateRunSummary,
    FollowUpCreate,
    FollowUpResponse,
    OperatorDebateInputCreate,
    OperatorDebateInputResponse,
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
    db: Session = Depends(get_db),
):
    query = db.query(WorkItem)
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


@router.get(
    "/{work_item_id}/debates",
    response_model=List[DebateRunSummary],
)
def list_debate_runs(work_item_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, work_item_id)
    return (
        db.query(DebateRun)
        .filter(DebateRun.work_item_id == work_item_id)
        .order_by(DebateRun.id.desc())
        .all()
    )


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
def execute_debate(
    work_item_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    """Execute a queued or failed debate run.

    If execution bridge is not configured, returns the run with status='queued'
    and a clear error message.

    If already running, returns current status.
    If completed, returns existing result (does not re-execute).
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

    # Execute the debate
    execute_debate_run(db, run, item, config)
    db.commit()
    db.refresh(run)
    return run


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
