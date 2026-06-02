from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..database import get_db
from ..lifecycle import compute_effective_state
from .. import preview_deploy, repo_safety
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
    WorkItemAttachment,
)
from ..schemas import (
    AttachmentResponse,
    BlockRequest,
    BulkArchiveRequest,
    BulkArchiveTestItemsRequest,
    CertifyRequest,
    CompleteRequest,
    DebateRunBulkHideRequest,
    DebateRunCreate,
    DebateRunDetail,
    DebateRunHideRequest,
    DebateRunHideResponse,
    DebateRunSummary,
    DeployPreviewRequest,
    FollowUpCreate,
    FollowUpResponse,
    MergeRequest,
    OperatorDebateInputCreate,
    OperatorDebateInputResponse,
    RejectRequest,
    RejectWithChangesRequest,
    RevertPreviewRequest,
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
    """Return the most relevant debate run for display.
    
    Priority:
    1. Active runs (queued/claimed/warming/running/generating) — show in-progress state
    2. Latest completed run by completed_at UTC — most recent terminal outcome
    3. Latest run by ID as fallback if timestamps missing
    
    Uses UTC completed_at for ordering, not display timezone.
    """
    # First check for active runs
    active_statuses = ["queued", "claimed", "warming", "running", "generating"]
    active = (
        db.query(DebateRun)
        .filter(
            DebateRun.work_item_id == work_item_id,
            DebateRun.status.in_(active_statuses)
        )
        .order_by(DebateRun.created_at.desc())
        .first()
    )
    if active:
        return active
    
    # No active run — get latest completed/failed/cancelled by completed_at
    terminal = (
        db.query(DebateRun)
        .filter(
            DebateRun.work_item_id == work_item_id,
            DebateRun.status.in_(["completed", "failed", "cancelled"])
        )
        .order_by(DebateRun.completed_at.desc().nullslast(), DebateRun.id.desc())
        .first()
    )
    if terminal:
        return terminal
    
    # Fallback: any run by ID
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
    response.effective_state = compute_effective_state(item, latest)
    return response


def _serialize_many_with_debate(
    db: Session, items: List[WorkItem]
) -> List[WorkItemResponse]:
    if not items:
        return []
    ids = [it.id for it in items]
    # Get latest debate for each work item using same logic as _latest_debate_for
    # Priority: active runs first, then latest by completed_at UTC
    from sqlalchemy import func as sa_func
    
    # First get active runs
    active_statuses = ["queued", "claimed", "warming", "running", "generating"]
    active_subq = (
        db.query(
            DebateRun.work_item_id,
            sa_func.max(DebateRun.created_at).label("max_created"),
        )
        .filter(
            DebateRun.work_item_id.in_(ids),
            DebateRun.status.in_(active_statuses)
        )
        .group_by(DebateRun.work_item_id)
        .subquery()
    )
    
    # Get terminal runs with latest completed_at
    terminal_subq = (
        db.query(
            DebateRun.work_item_id,
            sa_func.max(DebateRun.completed_at).label("max_completed"),
        )
        .filter(
            DebateRun.work_item_id.in_(ids),
            DebateRun.status.in_(["completed", "failed", "cancelled"])
        )
        .group_by(DebateRun.work_item_id)
        .subquery()
    )
    
    # Build runs dict: prefer active, then terminal
    runs_by_work_item = {}
    
    # Load active runs
    if active_subq is not None:
        active_runs = (
            db.query(DebateRun)
            .join(active_subq, 
                  (DebateRun.work_item_id == active_subq.c.work_item_id) &
                  (DebateRun.created_at == active_subq.c.max_created))
            .all()
        )
        for r in active_runs:
            runs_by_work_item[r.work_item_id] = r
    
    # Load terminal runs only for items without active runs
    terminal_ids = [wid for wid in ids if wid not in runs_by_work_item]
    if terminal_ids:
        terminal_runs = (
            db.query(DebateRun)
            .join(terminal_subq,
                  (DebateRun.work_item_id == terminal_subq.c.work_item_id) &
                  ((DebateRun.completed_at == terminal_subq.c.max_completed) | (DebateRun.completed_at == None)))
            .filter(DebateRun.work_item_id.in_(terminal_ids))
            .all()
        )
        # Pick the one with latest completed_at (or highest ID if null)
        for r in terminal_runs:
            if r.work_item_id not in runs_by_work_item:
                runs_by_work_item[r.work_item_id] = r
            elif r.completed_at is not None and runs_by_work_item[r.work_item_id].completed_at is not None:
                if r.completed_at > runs_by_work_item[r.work_item_id].completed_at:
                    runs_by_work_item[r.work_item_id] = r
                elif r.completed_at == runs_by_work_item[r.work_item_id].completed_at:
                    if r.id > runs_by_work_item[r.work_item_id].id:
                        runs_by_work_item[r.work_item_id] = r
    
    out: List[WorkItemResponse] = []
    for it in items:
        resp = WorkItemResponse.model_validate(it)
        run = runs_by_work_item.get(it.id)
        if run is not None:
            resp.latest_debate = DebateRunSummary.model_validate(run)
        resp.effective_state = compute_effective_state(it, run)
        out.append(resp)
    return out


@router.get("", response_model=List[WorkItemResponse])
def list_work_items(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    view: str = Query("active", description="active|archived|all"),
    generated: str = Query("all", description="human|system|test|all"),
    app: Optional[str] = Query(None, description="Filter by target app (app_id value)"),
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
    if app is not None:
        if app == "__unassigned__":
            query = query.filter(WorkItem.target_app.is_(None))
        else:
            query = query.filter(WorkItem.target_app == app)
    items = query.order_by(WorkItem.id.desc()).all()
    return _serialize_many_with_debate(db, items)


@router.get("/apps", response_model=List[str])
def list_work_item_apps(db: Session = Depends(get_db)):
    """Return distinct non-null target_app values from work items."""
    rows = (
        db.query(WorkItem.target_app)
        .filter(WorkItem.target_app.isnot(None))
        .distinct()
        .order_by(WorkItem.target_app)
        .all()
    )
    return [row[0] for row in rows]


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
    # Reconcile status: approved items should not remain DRAFT
    if item.status.lower() in ("draft", "debated"):
        item.status = "approved"
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


# ---------------------------------------------------------------------------
# Operator certification / rejection / change-request endpoints (slice 2)
# ---------------------------------------------------------------------------
#
# These actions reflect operator-only decisions on the Work Item and never
# touch the underlying PR (no merge, close, or branch ops). The effective
# state surfaces the result; later slices may use these flags as gates.

# Effective states from which an operator can certify, reject, or request
# changes. Anything else (drafting, building, merged, certified, rejected,
# needs_rework, archived, etc.) is a 409.
_CERTIFICATION_SOURCE_STATES = frozenset(
    {"in_review", "preview_ready", "code_reviewed"}
)


def _ensure_certification_source_state(item: WorkItem) -> str:
    """Validate the item is in a state where certification actions apply.

    Returns the computed effective_state on success; raises 409 otherwise.
    The latest debate is intentionally ignored — debate state never gates a
    certification decision.
    """
    state = compute_effective_state(item, None)
    if state not in _CERTIFICATION_SOURCE_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Work item is in state '{state}'; certification actions "
                "require state in_review, preview_ready, or code_reviewed."
            ),
        )
    return state


@router.post("/{work_item_id}/certify", response_model=WorkItemResponse)
def certify_work_item(
    work_item_id: int,
    payload: CertifyRequest,
    db: Session = Depends(get_db),
):
    item = _get_or_404(db, work_item_id)
    _ensure_certification_source_state(item)

    item.operator_certified = True
    item.certified_at = datetime.utcnow()
    item.certification_note = payload.certification_note
    db.commit()
    db.refresh(item)
    return _serialize_with_debate(db, item)


@router.post("/{work_item_id}/reject", response_model=WorkItemResponse)
def reject_work_item(
    work_item_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
):
    item = _get_or_404(db, work_item_id)
    _ensure_certification_source_state(item)

    item.rejected_at = datetime.utcnow()
    item.rejection_reason = payload.rejection_reason
    db.commit()
    db.refresh(item)
    return _serialize_with_debate(db, item)


@router.post(
    "/{work_item_id}/reject-with-changes",
    response_model=WorkItemResponse,
)
def reject_work_item_with_changes(
    work_item_id: int,
    payload: RejectWithChangesRequest,
    db: Session = Depends(get_db),
):
    item = _get_or_404(db, work_item_id)
    _ensure_certification_source_state(item)

    item.changes_requested_at = datetime.utcnow()
    item.change_request = payload.change_request
    # Clear stale review-ready signals so the lifecycle returns ``needs_rework``
    # instead of remaining pinned at ``code_reviewed`` or ``preview_ready`` —
    # those signals predate this change request and would otherwise outrank it
    # in precedence (see lifecycle.compute_effective_state). ``preview_required``
    # is also cleared so the lifecycle does not fall through to
    # ``preview_pending`` (which outranks ``needs_rework``) once the deploy
    # flag is gone.
    item.code_review_status = None
    item.preview_deployed = False
    item.preview_required = False
    db.commit()
    db.refresh(item)
    return _serialize_with_debate(db, item)


# ---------------------------------------------------------------------------
# Preview deployment / revert endpoints (slice 4)
# ---------------------------------------------------------------------------
#
# Operators trigger ``deploy-preview`` to bring up a Work Item's PR branch on
# the host's compose stack and ``revert-preview`` to roll back to the base
# branch. Both actions go through the same repo safety gate that builder
# runs use (clean worktree + free lock), acquire the lock for the duration
# of the action, and release it whether the action succeeds or aborts. The
# shell-out helpers live in :mod:`app.preview_deploy` so tests can stub the
# subprocesses out without touching the surrounding state machine.


def _preview_gate_or_409(
    db: Session, repo_path: str, repo_name: str, branch_name: str
) -> repo_safety.LockResult:
    """Run the repo safety gate and acquire the preview lock.

    Mirrors the builder gate (clean repo check, busy lock check, atomic
    acquire) and raises HTTPException(409) with a structured detail body on
    any blocker. On success the caller owns the returned lock and must
    release it before returning.
    """
    safety = repo_safety.check_repo_clean(repo_path)
    if not safety.is_clean:
        raise HTTPException(
            status_code=409,
            detail={
                "blocker_code": safety.blocker_code or "blocked_dirty_repo",
                "blocker_message": (
                    safety.blocker_message
                    or f"Repo {repo_path} is not clean"
                ),
                "repo_path": repo_path,
                "dirty_files": safety.dirty_files,
                "staged_files": safety.staged_files,
                "untracked_files": safety.untracked_files,
                "current_branch": safety.current_branch,
                "current_commit": safety.current_commit,
            },
        )

    existing_lock = repo_safety.check_repo_busy(db, repo_path)
    if existing_lock is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "blocker_code": "blocked_repo_busy",
                "blocker_message": (
                    f"Repo {repo_path} is already locked by an "
                    f"in-flight build (lock id {existing_lock.id})"
                ),
                "repo_path": repo_path,
                "existing_lock_id": existing_lock.id,
                "existing_lock_branch": existing_lock.branch_name,
                "existing_lock_work_item_id": existing_lock.work_item_id,
            },
        )

    lock_result = repo_safety.acquire_repo_lock(
        session=db,
        repo_path=repo_path,
        repo_name=repo_name,
        branch_name=branch_name,
        commit_sha=safety.current_commit or "unknown",
        lock_owner="preview-deploy",
    )
    if not lock_result.acquired:
        raise HTTPException(
            status_code=409,
            detail={
                "blocker_code": lock_result.blocker_code or "blocked_repo_busy",
                "blocker_message": (
                    lock_result.blocker_message
                    or f"Repo {repo_path} became busy during lock acquire"
                ),
                "repo_path": repo_path,
            },
        )
    return lock_result


@router.post("/{work_item_id}/deploy-preview", response_model=WorkItemResponse)
def deploy_preview_action(
    work_item_id: int,
    payload: Optional[DeployPreviewRequest] = None,
    db: Session = Depends(get_db),
):
    """Deploy a Work Item's PR branch as a preview on the host compose stack.

    Validates the Work Item carries branch / PR metadata, that its effective
    state allows a preview, and that the repo safety gate is open. On a
    successful build + healthcheck the Work Item is stamped as deployed; on
    any failure the lock is released and the preview is marked errored.
    """
    item = _get_or_404(db, work_item_id)

    if not item.branch_name or item.pr_number is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Work item is missing branch metadata. "
                "Both branch_name and pr_number must be set before "
                "deploying a preview."
            ),
        )

    state = compute_effective_state(item, None)
    if preview_deploy.is_preview_state_blocked(state):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Work item is in state '{state}'; preview deploy is not "
                "allowed for merged, implemented, certified, archived, "
                "rejected, or ready-to-merge items."
            ),
        )
    if not preview_deploy.is_deploy_state_allowed(state):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Work item is in state '{state}'; preview deploy requires "
                "an in-review, code-reviewed, preview, or rework state."
            ),
        )

    repo_path, repo_name = preview_deploy.resolve_preview_repo(item)
    lock_result = _preview_gate_or_409(
        db, repo_path, repo_name, item.branch_name
    )

    deployed_by = payload.deployed_by if payload else None
    item.preview_status = "deploying"
    item.preview_branch = item.branch_name
    item.preview_pr_number = item.pr_number
    item.preview_error = None
    db.commit()

    def _abort(reason: str, error_detail: str) -> None:
        """Mark the preview as errored and release the lock."""
        item.preview_status = "error"
        item.preview_error = error_detail[:2000]
        item.preview_health_status = "unhealthy"
        db.commit()
        repo_safety.release_repo_lock(
            db, repo_path, release_reason=reason, final_status="failed"
        )

    # Delegate the side-effectful work (git checkout, docker compose build/up,
    # healthcheck) to the host-side executor. The dashboard container mounts
    # /srv/repo read-only and cannot run docker compose against the host
    # daemon for arbitrary branches; only the executor can.
    result = preview_deploy.call_host_executor(
        action="deploy_preview",
        repo_path=repo_path,
        branch=item.branch_name,
    )
    if not result.success:
        reason = result.error_code or "preview_executor_failed"
        # The executor's ``error`` may include subprocess stderr — keep it
        # but trim to a sensible width before stamping it on the row.
        detail_msg = result.error or "host executor reported failure"
        _abort(reason, f"executor {reason}: {detail_msg}")
        raise HTTPException(
            status_code=502 if reason == "executor_unreachable" else 500,
            detail=f"Preview deploy failed: {detail_msg}",
        )

    # Successful deploy: stamp metadata and release the lock cleanly. The
    # executor returns ``commit_sha`` for the PR branch HEAD post-checkout,
    # so we record that as the deployed commit (the lock's commit_sha was
    # captured pre-checkout and reflects the base branch).
    now = datetime.utcnow()
    item.preview_status = "deployed"
    item.preview_deployed = True
    item.preview_deployed_at = now
    item.preview_deployed_by = deployed_by
    item.preview_health_status = result.health_status or "healthy"
    item.preview_commit_sha = result.commit_sha
    item.preview_error = None
    db.commit()
    db.refresh(item)

    repo_safety.release_repo_lock(
        db, repo_path, release_reason="preview_deployed", final_status="released"
    )

    return _serialize_with_debate(db, item)


@router.post("/{work_item_id}/revert-preview", response_model=WorkItemResponse)
def revert_preview_action(
    work_item_id: int,
    payload: RevertPreviewRequest,
    db: Session = Depends(get_db),
):
    """Revert a deployed preview back to the project's base branch.

    Requires that a preview is currently deployed. Runs the same safety gate
    as deploy (clean worktree + free lock), checks out the base branch,
    rebuilds the compose service, healthchecks, and clears the deployed
    flag. The revert reason is recorded for audit.
    """
    item = _get_or_404(db, work_item_id)

    if not item.preview_deployed:
        raise HTTPException(
            status_code=409,
            detail="Work item has no active preview deployment to revert.",
        )

    state = compute_effective_state(item, None)
    if preview_deploy.is_preview_state_blocked(state):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Work item is in state '{state}'; preview revert is not "
                "allowed for merged, implemented, certified, archived, "
                "rejected, or ready-to-merge items."
            ),
        )

    repo_path, repo_name = preview_deploy.resolve_preview_repo(item)
    lock_result = _preview_gate_or_409(
        db, repo_path, repo_name, preview_deploy.REVERT_BASE_BRANCH
    )

    reverted_by = payload.reverted_by
    reason = payload.reason

    item.preview_status = "reverting"
    db.commit()

    def _abort(release_reason: str, error_detail: str) -> None:
        item.preview_status = "revert_error"
        item.preview_error = error_detail[:2000]
        db.commit()
        repo_safety.release_repo_lock(
            db, repo_path, release_reason=release_reason, final_status="failed"
        )

    result = preview_deploy.call_host_executor(
        action="revert_preview",
        repo_path=repo_path,
        branch=preview_deploy.REVERT_BASE_BRANCH,
    )
    if not result.success:
        reason_code = result.error_code or "revert_executor_failed"
        detail_msg = result.error or "host executor reported failure"
        _abort(reason_code, f"executor {reason_code}: {detail_msg}")
        raise HTTPException(
            status_code=502 if reason_code == "executor_unreachable" else 500,
            detail=f"Preview revert failed: {detail_msg}",
        )

    now = datetime.utcnow()
    item.preview_status = "reverted"
    item.preview_deployed = False
    item.preview_health_status = "reverted"
    item.preview_reverted_at = now
    item.preview_reverted_by = reverted_by
    item.preview_revert_reason = reason
    item.preview_error = None
    db.commit()
    db.refresh(item)

    repo_safety.release_repo_lock(
        db, repo_path, release_reason="preview_reverted", final_status="released"
    )

    return _serialize_with_debate(db, item)


# ---------------------------------------------------------------------------
# Merge / complete endpoints (slice 5)
# ---------------------------------------------------------------------------
#
# ``merge`` lands a certified, ready-to-merge Work Item's PR via the host
# executor (gh pr merge + base branch deploy + healthcheck) and ``complete``
# closes out the lifecycle once the post-merge deploy has been verified.
# Both endpoints reuse the same repo safety gate as preview deploy.


# Effective states from which ``merge`` is allowed. ``blocked_merge`` is
# included so a prior failed merge attempt can be retried without manual DB
# surgery; the operator-supplied PR metadata still has to validate.
_MERGE_TERMINAL_BLOCK_STATES = frozenset(
    {"complete", "merged", "merged_deployment_failed", "archived"}
)


@router.post("/{work_item_id}/merge", response_model=WorkItemResponse)
def merge_work_item(
    work_item_id: int,
    payload: MergeRequest,
    db: Session = Depends(get_db),
):
    """Merge a certified, ready-to-merge Work Item's PR via the host executor.

    Validates that the Work Item is operator-certified AND ready-to-merge,
    that it carries PR metadata matching the operator's expectations, that
    the repo safety gate is open, and that the executor confirms the merge
    succeeded before stamping ``merge_commit_sha`` on the row.
    """
    item = _get_or_404(db, work_item_id)

    # Terminal-state guard: a merged / complete / archived item must not be
    # re-merged. Other guards below give more specific errors for items
    # that simply have not been certified yet.
    state = compute_effective_state(item, None)
    if state in _MERGE_TERMINAL_BLOCK_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Work item is in state '{state}'; merge is not allowed for "
                "merged, complete, merged_deployment_failed, or archived items."
            ),
        )

    if item.operator_certified is not True or item.ready_to_merge is not True:
        raise HTTPException(
            status_code=409,
            detail=(
                "Work item must be operator_certified AND ready_to_merge "
                "before merging."
            ),
        )

    if not preview_deploy.is_merge_state_allowed(state):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Work item is in state '{state}'; merge requires "
                "ready_to_merge, certified, or blocked_merge."
            ),
        )

    if not item.branch_name or item.pr_number is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Work item is missing PR metadata. Both branch_name and "
                "pr_number must be set before merging."
            ),
        )

    if payload.expected_pr_number != item.pr_number:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Expected PR #{payload.expected_pr_number} does not match "
                f"the Work Item's PR #{item.pr_number}."
            ),
        )
    if payload.expected_branch != item.branch_name:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Expected branch '{payload.expected_branch}' does not "
                f"match the Work Item's branch '{item.branch_name}'."
            ),
        )

    repo_path, repo_name = preview_deploy.resolve_preview_repo(item)
    lock_result = _preview_gate_or_409(
        db, repo_path, repo_name, payload.expected_base_branch
    )

    item.merge_status = "merging"
    item.merge_error = None
    db.commit()

    def _abort_no_sha(reason: str, error_detail: str) -> None:
        """Mark merge as failed BEFORE any SHA was recorded."""
        item.merge_status = "failed"
        item.merge_error = error_detail[:2000]
        db.commit()
        repo_safety.release_repo_lock(
            db, repo_path, release_reason=reason, final_status="failed"
        )

    result = preview_deploy.call_host_executor(
        action="merge_pr",
        repo_path=repo_path,
        branch=payload.expected_base_branch,
        pr_number=item.pr_number,
        base_branch=payload.expected_base_branch,
    )

    # Executor returned a merge SHA even though the post-merge work failed
    # (build / up / healthcheck). The merge itself landed — record the SHA
    # and surface ``merged_deployment_failed`` so the operator can fix the
    # deploy without retrying the merge.
    if result.merge_commit_sha:
        now = datetime.utcnow()
        item.merge_commit_sha = result.merge_commit_sha
        item.merged_at = now
        item.merged_by = payload.merged_by
        item.merge_note = payload.merge_note
        item.post_merge_verified_at = now
        item.post_merge_verified_by = payload.merged_by
        if result.success:
            item.merge_status = "merged"
            item.post_merge_health_status = result.health_status or "healthy"
            item.merge_error = None
            db.commit()
            db.refresh(item)
            repo_safety.release_repo_lock(
                db,
                repo_path,
                release_reason="merge_succeeded",
                final_status="released",
            )
            return _serialize_with_debate(db, item)

        # Merge SHA present but executor failure → ``merged_deployment_failed``.
        item.merge_status = "merged_deployment_failed"
        item.post_merge_health_status = result.health_status or "unhealthy"
        item.merge_error = (
            f"executor {result.error_code or 'merge_post_failed'}: "
            f"{result.error or 'post-merge step failed'}"
        )[:2000]
        db.commit()
        db.refresh(item)
        repo_safety.release_repo_lock(
            db,
            repo_path,
            release_reason=result.error_code or "merge_post_failed",
            final_status="failed",
        )
        raise HTTPException(
            status_code=500,
            detail=(
                "Merge landed but post-merge deploy failed: "
                f"{result.error or 'unknown error'}"
            ),
        )

    # No merge SHA — the merge never happened. Surface a blocked_merge state.
    reason = result.error_code or "merge_executor_failed"
    detail_msg = result.error or "host executor reported failure"
    _abort_no_sha(reason, f"executor {reason}: {detail_msg}")
    raise HTTPException(
        status_code=502 if reason == "executor_unreachable" else 500,
        detail=f"Merge failed: {detail_msg}",
    )


@router.post("/{work_item_id}/complete", response_model=WorkItemResponse)
def complete_work_item(
    work_item_id: int,
    payload: CompleteRequest,
    db: Session = Depends(get_db),
):
    """Mark a merged Work Item as complete after post-merge verification.

    Requires that a merge SHA is recorded, that the effective state is
    ``merged``, and that the post-merge healthcheck succeeded. Records
    completion metadata; no executor call.
    """
    item = _get_or_404(db, work_item_id)

    if not item.merge_commit_sha:
        raise HTTPException(
            status_code=409,
            detail="Work item has no merge_commit_sha; cannot mark complete.",
        )

    state = compute_effective_state(item, None)
    if state == "complete":
        raise HTTPException(
            status_code=409,
            detail="Work item is already complete.",
        )
    if state != "merged":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Work item is in state '{state}'; complete requires state "
                "'merged' with a passing post-merge healthcheck."
            ),
        )

    health = (item.post_merge_health_status or "").lower()
    if health != "healthy":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Post-merge health status is '{item.post_merge_health_status}'; "
                "complete requires a passing post-merge healthcheck."
            ),
        )

    now = datetime.utcnow()
    item.completed_at = now
    item.completed_by = payload.completed_by
    item.completion_note = payload.completion_note
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
# Attachment endpoints
# ---------------------------------------------------------------------------

import os
import uuid
from pathlib import Path

# Configurable via env vars with sensible defaults
ATTACHMENT_STORAGE_PATH = Path(os.environ.get("ATTACHMENT_STORAGE_PATH", "/app/attachments"))
ATTACHMENT_MAX_FILE_SIZE = int(os.environ.get("ATTACHMENT_MAX_FILE_SIZE", str(10 * 1024 * 1024)))  # 10 MB
ATTACHMENT_ALLOWED_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}


def _ensure_storage_dir() -> None:
    ATTACHMENT_STORAGE_PATH.mkdir(parents=True, exist_ok=True)


@router.get(
    "/{work_item_id}/attachments",
    response_model=List[AttachmentResponse],
)
def list_attachments(work_item_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, work_item_id)
    attachments = (
        db.query(WorkItemAttachment)
        .filter(WorkItemAttachment.work_item_id == work_item_id)
        .order_by(WorkItemAttachment.created_at.desc())
        .all()
    )
    return attachments


@router.post(
    "/{work_item_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    work_item_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload an attachment to a work item.

    Accepts PNG, JPG, GIF, WEBP images and PDF files.
    Max file size: 10 MB (configurable via ATTACHMENT_MAX_FILE_SIZE env var).
    """
    item = _get_or_404(db, work_item_id)

    # Validate content type
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ATTACHMENT_ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type: {content_type}. Allowed: {', '.join(sorted(ATTACHMENT_ALLOWED_TYPES.keys()))}",
        )

    # Read file content
    content = await file.read()

    # Validate file size
    file_size = len(content)
    if file_size == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if file_size > ATTACHMENT_MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {file_size} bytes. Maximum: {ATTACHMENT_MAX_FILE_SIZE} bytes ({ATTACHMENT_MAX_FILE_SIZE // (1024*1024)} MB).",
        )

    # Magic-byte validation: ensure the file's actual bytes match the declared content type.
    # Defends against clients spoofing content_type to slip disallowed payloads past the
    # MIME-only check above.
    _MAGIC_BYTES = {
        "image/png": (0, bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])),
        "image/jpeg": (0, bytes([0xFF, 0xD8, 0xFF])),
        "image/gif": (0, bytes([0x47, 0x49, 0x46])),
        "image/webp": (4, bytes([0x57, 0x45, 0x42, 0x50])),
        "application/pdf": (0, bytes([0x25, 0x50, 0x44, 0x46, 0x2D])),
    }
    offset, signature = _MAGIC_BYTES[content_type]
    if len(content) < offset + len(signature) or content[offset:offset + len(signature)] != signature:
        raise HTTPException(status_code=400, detail="File content does not match declared type")

    # Sanitize filename: strip path components, limit length
    original_filename = os.path.basename(file.filename or "attachment")
    if len(original_filename) > 200:
        original_filename = original_filename[-200:]

    # Generate unique storage filename
    ext = ATTACHMENT_ALLOWED_TYPES[content_type]
    storage_filename = f"{uuid.uuid4().hex}{ext}"
    storage_path = ATTACHMENT_STORAGE_PATH / storage_filename

    # Write file to disk
    _ensure_storage_dir()
    try:
        with open(storage_path, "wb") as f:
            f.write(content)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {type(e).__name__}")

    # Create DB record
    attachment = WorkItemAttachment(
        work_item_id=work_item_id,
        filename=storage_filename,
        original_filename=original_filename,
        content_type=content_type,
        file_size=file_size,
        storage_path=str(storage_path),
    )
    db.add(attachment)
    try:
        db.commit()
    except Exception:
        db.rollback()
        # Roll back the filesystem side too so we don't leave an orphan blob.
        try:
            if storage_path.exists():
                storage_path.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="Failed to save attachment record")
    db.refresh(attachment)
    return attachment


@router.get(
    "/{work_item_id}/attachments/{attachment_id}",
    response_class=FileResponse,
)
def download_attachment(
    work_item_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
):
    """Download an attachment by ID. Validates that the attachment belongs to the specified work item."""
    _get_or_404(db, work_item_id)
    attachment = (
        db.query(WorkItemAttachment)
        .filter(
            WorkItemAttachment.id == attachment_id,
            WorkItemAttachment.work_item_id == work_item_id,
        )
        .first()
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    file_path = Path(attachment.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Attachment file missing from storage")

    return FileResponse(
        path=str(file_path),
        filename=attachment.original_filename,
        media_type=attachment.content_type,
    )


@router.delete(
    "/{work_item_id}/attachments/{attachment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_attachment(
    work_item_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
):
    """Delete an attachment. Removes both the DB record and the stored file."""
    _get_or_404(db, work_item_id)
    attachment = (
        db.query(WorkItemAttachment)
        .filter(
            WorkItemAttachment.id == attachment_id,
            WorkItemAttachment.work_item_id == work_item_id,
        )
        .first()
    )
    if attachment is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # Remove file from disk (best-effort)
    try:
        file_path = Path(attachment.storage_path)
        if file_path.exists():
            file_path.unlink()
    except OSError:
        pass  # Continue even if file deletion fails

    db.delete(attachment)
    db.commit()


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
    if run.status in ("running", "warming", "generating"):
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


@router.post(
    "/{work_item_id}/debates/{run_id}/cancel",
    response_model=DebateRunDetail,
)
def cancel_debate_run(
    work_item_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    """Request cancellation of a running debate run.
    
    Sets cancel_requested flag. Worker will stop at next safe point.
    """
    _get_or_404(db, work_item_id)
    run = (
        db.query(DebateRun)
        .filter(DebateRun.id == run_id, DebateRun.work_item_id == work_item_id)
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Debate run not found")
    
    # Can only cancel active runs
    if run.status not in ("queued", "running", "warming", "generating", "claimed"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel run with status '{run.status}'")
    
    run.cancel_requested = True
    db.commit()
    db.refresh(run)
    return run


@router.post(
    "/{work_item_id}/debates/{run_id}/retry",
    response_model=DebateRunDetail,
    status_code=status.HTTP_201_CREATED,
)
def retry_debate_run(
    work_item_id: int,
    run_id: int,
    db: Session = Depends(get_db),
):
    """Create a new debate run that retries a failed run.
    
    Links to original via retry_of_run_id.
    """
    item = _get_or_404(db, work_item_id)
    original_run = (
        db.query(DebateRun)
        .filter(DebateRun.id == run_id, DebateRun.work_item_id == work_item_id)
        .first()
    )
    if original_run is None:
        raise HTTPException(status_code=404, detail="Debate run not found")
    
    # Create retry run
    from datetime import datetime, timezone
    retry_run = DebateRun(
        work_item_id=work_item_id,
        work_item_type_snapshot=original_run.work_item_type_snapshot,
        status="queued",
        worker_status="queued",
        rounds_requested=original_run.rounds_requested,
        trigger="manual_rerun",
        retry_of_run_id=original_run.id,
        attempt_number=original_run.attempt_number + 1,
        queued_at=datetime.now(timezone.utc)
    )
    db.add(retry_run)
    db.commit()
    db.refresh(retry_run)
    return retry_run


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
