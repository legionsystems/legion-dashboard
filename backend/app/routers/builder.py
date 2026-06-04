"""Builder Task router - Hermes Kanban bridge."""
import json
import os
import subprocess
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..builder_status_projection import sync_work_item_status_from_builder
from ..builder_card import (
    build_implementation_card_prompt,
    load_arbiter_mandatory_edits,
)
from ..worktree_paths import (
    build_task_worktree_path,
    is_shared_repo_path,
)
from ..task_worktree import ensure_task_worktree
from ..models import BuilderTask, DebateRun, RepoLock, WorkItem
from .. import repo_safety
from ..schemas_builder import BuilderTaskResponse, SendToBuilderRequest

router = APIRouter(prefix="/api/builder", tags=["builder"])


def _resolve_target_repo_path(work_item: WorkItem) -> str:
    """Derive the on-host target repo path from the work item's target_app.

    Kept as a small helper so both the prompt assembly and the safety
    gate agree on the same path.
    """
    if work_item.target_app and "hub" in work_item.target_app.lower():
        return "/srv/repo/lgn-hub"
    return "/srv/repo/legion-dashboard"


def _compute_task_worktree_path(
    work_item: WorkItem,
    builder_task_id: Optional[int] = None,
) -> str:
    """Return the task-specific worktree path the builder should use.

    This is the SINGLE place that decides what worktree a given
    builder task is assigned to. The shared operator/control worktree
    is never returned for an implementation task.
    """
    path = build_task_worktree_path(
        work_item, builder_task_id=builder_task_id
    )
    # Defensive: a future regression in the helper could conceivably
    # produce a path that collides with the shared operator/control
    # repo. Fail loudly so the operator sees the misrouting rather
    # than the builder working in the wrong worktree.
    if is_shared_repo_path(path):
        raise RuntimeError(
            f"Task worktree path {path!r} collides with a shared "
            f"operator/control repo path. Refusing to generate a "
            f"builder prompt for work item {work_item.id}."
        )
    return path


def _generate_hermes_prompt(
    work_item: WorkItem,
    db: Optional[Session] = None,
    debate_run_id: Optional[int] = None,
    recommendation: Optional[str] = None,
    implementation_readiness: Optional[str] = None,
    mandatory_edits: Optional[list] = None,
    builder_task_id: Optional[int] = None,
    feature_branch: Optional[str] = None,
    chosen_base_ref: Optional[str] = None,
) -> str:
    """Generate the Hermes Kanban implementation card body.

    The canonical assembly lives in :mod:`app.builder_card`. This
    adapter exists so callers that previously passed
    ``mandatory_edits_json`` still work, and so the prompt is built
    from the structured source (the Final Arbiter's JSON, parsed from
    the latest ``DebateArgument``) rather than the previous misuse of
    ``DebateRun.summary`` (which stores the rationale text).

    The generated prompt ALWAYS targets a task-specific worktree
    under ``/srv/worktrees/``. The shared operator/control worktree
    is reserved for operator/control work only and is never used as
    the implementation target.
    """
    target_worktree = _compute_task_worktree_path(
        work_item, builder_task_id=builder_task_id
    )
    # ``target_repo`` stays in sync with the worktree so the rest of
    # the prompt (PHASE 1, secret scan, Codex review) sees the same
    # path. Keeping the alias also preserves the safety gate
    # contract: it expects a path it can ``cd`` into.
    target_repo = target_worktree

    # If the caller passed an explicit feature branch, surface it in
    # the prompt. Otherwise leave it unset; the builder card's
    # WORKTREE METADATA block reports the omission so the
    # operator can see the orchestrator's branch name does not
    # round-trip.
    feature_branch_kwarg = feature_branch

    # Resolve mandatory edits in priority order:
    #   1. explicit ``mandatory_edits`` list passed in
    #   2. the structured arbiter JSON from the latest DebateArgument
    #   3. empty (APPROVE_AS_IS / no debate)
    edits: list = []
    if mandatory_edits is not None:
        edits = list(mandatory_edits)
    elif db is not None and debate_run_id is not None:
        debate_run = (
            db.query(DebateRun).filter(DebateRun.id == debate_run_id).first()
        )
        if debate_run is not None:
            edits = load_arbiter_mandatory_edits(db, debate_run)

    return build_implementation_card_prompt(
        work_item,
        debate_run_id=debate_run_id,
        recommendation=recommendation,
        implementation_readiness=implementation_readiness,
        mandatory_edits=edits,
        target_repo=target_repo,
        target_worktree=target_worktree,
        feature_branch=feature_branch,
        chosen_base_ref=chosen_base_ref,
    )


def _create_hermes_task(title: str, body: str, assignee: Optional[str] = None, idempotency_key: Optional[str] = None, priority: Optional[str] = None, status_override: Optional[str] = None) -> dict:
    """Create Hermes Kanban task via Hermes Kanban Bridge HTTP API.
    
    Returns dict with task_id, status, or raises HTTPException on failure.
    
    Note: Uses the host-local Hermes Kanban Bridge (port 8765) which wraps
    the Hermes CLI. This avoids container/host binary incompatibility issues.
    """
    # Build request payload
    payload = {
        "title": title,
        "body": body,
        "assignee": assignee or "builder",
        "idempotency_key": idempotency_key or f"legion-dashboard-wi-{title.replace(' ', '-').lower()[:50]}",
    }
    
    # Priority must be integer for Hermes CLI
    if priority:
        try:
            payload["priority"] = int(priority)
        except (ValueError, TypeError):
            # Default to 0 if priority is not a valid integer
            payload["priority"] = 0
    
    # Status override: triage for send-to-builder, ready for start-build
    if status_override == "triage":
        payload["triage"] = True
    # For "ready" status, we don't set triage - task goes to ready by default
    
    # Call Hermes Kanban Bridge API
    import urllib.request
    import urllib.error
    
    bridge_url = "http://host.docker.internal:8765/tasks"
    try:
        req = urllib.request.Request(
            bridge_url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            result = json.loads(response.read().decode())
            task = result.get("task", {})
            return {
                "task_id": task.get("id"),
                "status": task.get("status", "ready"),
                "assignee": task.get("assignee"),
            }
    except urllib.error.HTTPError as e:
        error_body = e.read().decode()[:500]
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Hermes Kanban Bridge failed ({e.code}): {error_body}"
        )
    except urllib.error.URLError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Hermes Kanban Bridge unreachable: {e.reason}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Hermes Kanban Bridge error: {str(e)}"
        )


def _resolve_target_repo(work_item: WorkItem) -> tuple[str, str]:
    """Pick the (repo_path, repo_name) tuple for a work item.

    Mirrors the legacy ``_create_builder_task`` derivation so the safety gate
    and Hermes prompt resolve to the same repo.
    """
    if work_item.target_app and "hub" in work_item.target_app.lower():
        return ("/srv/repo/lgn-hub", "lgn-hub")
    return ("/srv/repo/legion-dashboard", "legion-dashboard")


def _create_builder_task(db: Session, work_item_id: int, request: SendToBuilderRequest, status_override: str = None) -> BuilderTaskResponse:
    """Internal helper to create builder task."""
    # Fetch work item
    work_item = db.query(WorkItem).filter(WorkItem.id == work_item_id).first()
    if not work_item:
        raise HTTPException(status_code=404, detail="Work item not found")

    # Validate approved state
    if not work_item.approved_by_operator:
        raise HTTPException(
            status_code=400,
            detail="Work item must be approved by operator before sending to builder"
        )

    # Block Note type by default
    if work_item.type.lower() == "note":
        raise HTTPException(
            status_code=400,
            detail="Note type cannot start build"
        )

    # Check for existing active builder task
    existing = db.query(BuilderTask).filter(
        BuilderTask.work_item_id == work_item_id,
        BuilderTask.hermes_status.not_in(["archived", "done"])
    ).first()

    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Active builder task already exists: {existing.hermes_task_id}"
        )

    # Get latest completed debate run for this work item. Pulled
    # up here so the BuilderTask stub row below can record the
    # debate metadata before we get to the prompt-generation
    # step.
    from ..models import DebateRun
    latest_debate = db.query(DebateRun).filter(
        DebateRun.work_item_id == work_item_id,
        DebateRun.status == "completed"
    ).order_by(DebateRun.completed_at.desc()).first()

    # ------------------------------------------------------------------
    # Per-task worktree allocation. The implementation Kanban card
    # body MUST point the builder at a dedicated worktree under
    # ``/srv/worktrees/`` rather than the shared operator/control
    # worktree, so we create (or reuse) the worktree BEFORE we
    # run the safety gate. The gate then inspects the actual
    # checkout the builder will operate in (the task worktree),
    # not the shared repo the builder is about to leave.
    #
    # Per-attempt identity comes from the BuilderTask auto-increment
    # id (allocated by ``db.add(...); db.flush()`` below). The DB
    # autoincrement is the single source of truth for uniqueness:
    # even if two worker retries for the same work item somehow
    # both pass the active-task check (e.g. the previous attempt
    # is already ``done``/``archived``), each gets a distinct
    # ``builder_task.id``, a distinct worktree path, and a
    # distinct feature branch. This solves retry/duplicate
    # branch collision without a multi-user locking system.
    # ------------------------------------------------------------------
    mandatory_edits_for_record: list = []
    if latest_debate is not None:
        mandatory_edits_for_record = load_arbiter_mandatory_edits(
            db, latest_debate
        )
    builder_task = BuilderTask(
        work_item_id=work_item_id,
        # Placeholder hermes_task_id — overwritten with the real
        # value once _create_hermes_task() returns. Using a
        # unique sentinel keeps the row visible in the
        # active-task list while signalling "not yet handed off
        # to Hermes" to any reader.
        hermes_task_id=f"pending-{work_item_id}-{int(datetime.utcnow().timestamp())}",
        hermes_board="legion-apps-build-queue",
        hermes_status="creating",
        hermes_assignee=request.hermes_assignee or "builder",
        title=work_item.title,
        target_app=work_item.target_app,
        target_repo=None,
        priority=request.priority or work_item.priority,
        debate_run_id=latest_debate.id if latest_debate else None,
        recommendation=latest_debate.final_recommendation if latest_debate else None,
        implementation_readiness=latest_debate.implementation_readiness if latest_debate else None,
        mandatory_edits_json=(
            json.dumps(mandatory_edits_for_record)
            if mandatory_edits_for_record
            else None
        ),
    )
    db.add(builder_task)
    # Flush so the autoincrement id is populated. We do NOT commit
    # yet — the row is still mutable, and we will not return it
    # to the caller until after the worktree allocation, the
    # safety check, the repo lock, the prompt generation, and
    # the Hermes task creation all succeed.
    db.flush()
    db.refresh(builder_task)
    attempt_id = builder_task.id
    try:
        (
            target_worktree,
            feature_branch,
            chosen_base_ref,
            _wt_result,
        ) = ensure_task_worktree(
            db, work_item, builder_task_id=attempt_id
        )
    except RuntimeError as exc:
        # Worktree creation failed. Roll back the stub BuilderTask
        # row so it does not appear in the active-task list and
        # does not block a fresh retry.
        db.delete(builder_task)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail={
                "blocker_code": "blocked_worktree_create_failed",
                "blocker_message": str(exc),
                "work_item_id": work_item_id,
            },
        )
    # Save the orchestrator's metadata on the BuilderTask so the
    # task record is self-describing and operators can audit
    # which worktree/branch/base ref this task used.
    builder_task.target_repo = target_worktree
    builder_task.generated_prompt_snapshot = None  # set after prompt is generated

    # ------------------------------------------------------------------
    # Repo safety gate, retargeted at the task worktree. The
    # /srv/repo/legion-dashboard shared checkout is the
    # operator/control worktree. For worktree-isolated builds the
    # path the builder will operate in is the new task worktree.
    # Shared-repo checks (which would block because the operator
    # may have other dirty work in the shared checkout) are NOT
    # applied to the implementation path. The gate here is the
    # last line of defence against an already-dirty task
    # worktree leaking into the build (e.g. a previous builder
    # crashed mid-edit). Triage-only sends still want the gate's
    # dirty/branch feedback but skip the lock (mirroring the
    # original behaviour).
    # ------------------------------------------------------------------
    target_repo = target_worktree
    skip_gate = os.environ.get("LEGION_SKIP_REPO_SAFETY_GATE") == "1"
    # Triage-only sends queue a Hermes card without starting a
    # build, so they must not lock the repo (or be blocked by an
    # in-flight build's lock). We still run the dirty/branch
    # checks so an operator sees the same gate feedback whether
    # they triage or start immediately.
    skip_lock = status_override == "triage"
    acquired_lock: Optional[RepoLock] = None
    # _resolve_target_repo returns the shared operator/control
    # repo path; we use the slug for the lock-name table and
    # otherwise point the gate at the task worktree.
    _, repo_name = _resolve_target_repo(work_item)
    if not skip_gate:
        safety = repo_safety.check_repo_clean_via_executor(target_repo)
        if not safety.is_clean:
            raise HTTPException(
                status_code=409,
                detail={
                    "blocker_code": safety.blocker_code or "blocked_dirty_repo",
                    "blocker_message": (
                        safety.blocker_message
                        or f"Repo {target_repo} is not clean"
                    ),
                    "repo_path": target_repo,
                    "dirty_files": safety.dirty_files,
                    "staged_files": safety.staged_files,
                    "untracked_files": safety.untracked_files,
                    "current_branch": safety.current_branch,
                    "current_commit": safety.current_commit,
                },
            )

        if (
            work_item.branch_name
            and safety.current_branch
            and work_item.branch_name != safety.current_branch
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "blocker_code": "blocked_branch_mismatch",
                    "blocker_message": (
                        f"Repo {target_repo} is on branch "
                        f"'{safety.current_branch}' but work item expects "
                        f"'{work_item.branch_name}'"
                    ),
                    "repo_path": target_repo,
                    "current_branch": safety.current_branch,
                    "expected_branch": work_item.branch_name,
                },
            )

        if not skip_lock:
            existing_lock = repo_safety.check_repo_busy(db, target_repo)
            if existing_lock is not None:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "blocker_code": "blocked_repo_busy",
                        "blocker_message": (
                            f"Repo {target_repo} is already locked by an "
                            f"in-flight build (lock id {existing_lock.id})"
                        ),
                        "repo_path": target_repo,
                        "existing_lock_id": existing_lock.id,
                        "existing_lock_branch": existing_lock.branch_name,
                        "existing_lock_work_item_id": existing_lock.work_item_id,
                    },
                )

            lock_result = repo_safety.acquire_repo_lock(
                session=db,
                repo_path=target_repo,
                repo_name=repo_name,
                branch_name=safety.current_branch or "unknown",
                commit_sha=safety.current_commit or "unknown",
                work_item_id=work_item_id,
                task_id=None,
                lock_owner=request.hermes_assignee or "builder",
            )
            if not lock_result.acquired:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "blocker_code": lock_result.blocker_code or "blocked_repo_busy",
                        "blocker_message": (
                            lock_result.blocker_message
                            or f"Repo {target_repo} became busy during lock acquire"
                        ),
                        "repo_path": target_repo,
                    },
                )
            acquired_lock = lock_result.lock

    # Generate prompt. Pass the same builder_task.id and the same
    # feature branch we used to create the worktree so the prompt
    # body renders the exact path and branch the orchestrator just
    # provisioned. Without this, the prompt would point the
    # builder at a different (uncreated) worktree and on retry
    # would re-use a branch that ``git worktree add`` cannot check
    # out a second time.
    prompt_body = _generate_hermes_prompt(
        work_item=work_item,
        db=db,
        debate_run_id=latest_debate.id if latest_debate else None,
        recommendation=latest_debate.final_recommendation if latest_debate else None,
        implementation_readiness=latest_debate.implementation_readiness if latest_debate else None,
        builder_task_id=attempt_id,
        feature_branch=feature_branch,
        chosen_base_ref=chosen_base_ref,
    )

    # Create Hermes task. If this fails we must release the lock we just
    # acquired so the repo doesn't stay pinned to a build that never started.
    idempotency_key = f"legion-dashboard-work-item-{work_item_id}-builder-task-{attempt_id}-v1"
    try:
        hermes_result = _create_hermes_task(
            title=f"LEGION-WI-{work_item_id} — {work_item.title[:100]}",
            body=prompt_body,
            assignee=request.hermes_assignee or "builder",
            idempotency_key=idempotency_key,
            priority=request.priority or work_item.priority,
            status_override=status_override,
        )
    except Exception:
        if acquired_lock is not None:
            repo_safety.release_repo_lock(
                db,
                target_repo,
                release_reason="hermes_task_create_failed",
                final_status="failed",
            )
        # Roll back the stub BuilderTask row so a fresh retry can
        # allocate a new builder_task.id (and therefore a new
        # worktree + branch).
        db.delete(builder_task)
        db.commit()
        raise

    # Populate the BuilderTask stub row with the Hermes metadata
    # and the generated prompt snapshot, then commit.
    builder_task.hermes_task_id = hermes_result["task_id"]
    builder_task.hermes_board = "legion-apps-build-queue"
    builder_task.hermes_status = hermes_result["status"]
    builder_task.hermes_assignee = request.hermes_assignee or "builder"
    builder_task.target_repo = target_worktree
    builder_task.generated_prompt_snapshot = prompt_body
    db.commit()
    db.refresh(builder_task)

    # Backfill the lock's task_id now that we have the Hermes ID.
    if acquired_lock is not None and hermes_result.get("task_id"):
        acquired_lock.task_id = str(hermes_result["task_id"])
        db.commit()

    # Project Hermes status to Work Item status (reusable lifecycle transition)
    sync_work_item_status_from_builder(
        db,
        builder_task.work_item_id,  # type: ignore[arg-type]
        builder_task.hermes_status,  # type: ignore[arg-type]
    )

    return builder_task


@router.post("/work-items/{work_item_id}/send-to-builder", response_model=BuilderTaskResponse)
def send_to_builder(
    work_item_id: int,
    request: SendToBuilderRequest,
    db: Session = Depends(get_db),
):
    """Send an approved work item to Hermes Kanban builder (triage status)."""
    return _create_builder_task(db, work_item_id, request, status_override="triage")


@router.post("/work-items/{work_item_id}/start-build", response_model=BuilderTaskResponse)
def start_build(
    work_item_id: int,
    request: SendToBuilderRequest,
    db: Session = Depends(get_db),
):
    """Start build on an approved work item (ready status, auto-starts via Hermes)."""
    return _create_builder_task(db, work_item_id, request, status_override="ready")


@router.get("/work-items/{work_item_id}/builder", response_model=Optional[BuilderTaskResponse])
def get_builder_task(
    work_item_id: int,
    db: Session = Depends(get_db),
):
    """Get linked builder task for a work item."""
    builder_task = db.query(BuilderTask).filter(
        BuilderTask.work_item_id == work_item_id
    ).order_by(BuilderTask.created_at.desc()).first()
    
    return builder_task


@router.get("/tasks", response_model=list[BuilderTaskResponse])
def list_builder_tasks(
    work_item_id: Optional[int] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List builder tasks with optional filters."""
    query = db.query(BuilderTask)
    
    if work_item_id:
        query = query.filter(BuilderTask.work_item_id == work_item_id)
    
    if status:
        query = query.filter(BuilderTask.hermes_status == status)
    
    return query.order_by(BuilderTask.created_at.desc()).all()


@router.get("/tasks/{task_id}", response_model=BuilderTaskResponse)
def get_builder_task_by_id(
    task_id: int,
    db: Session = Depends(get_db),
):
    """Get a specific builder task by ID."""
    builder_task = db.query(BuilderTask).filter(BuilderTask.id == task_id).first()
    if not builder_task:
        raise HTTPException(status_code=404, detail="Builder task not found")
    
    return builder_task


@router.post("/tasks/{task_id}/sync", response_model=BuilderTaskResponse)
def sync_builder_task(
    task_id: int,
    db: Session = Depends(get_db),
):
    """Sync builder task status from Hermes Kanban."""
    builder_task = db.query(BuilderTask).filter(BuilderTask.id == task_id).first()
    if not builder_task:
        raise HTTPException(status_code=404, detail="Builder task not found")
    
    # Query Hermes for current task status via bridge API (not docker run)
    import urllib.request
    import urllib.error
    
    bridge_url = f"http://host.docker.internal:8765/tasks/{builder_task.hermes_task_id}"
    try:
        req = urllib.request.Request(bridge_url, method="GET")
        with urllib.request.urlopen(req, timeout=30) as response:
            hermes_data = json.loads(response.read().decode())
    except Exception:
        # Fall back to docker run if bridge unreachable
        cmd = [
            "docker", "run", "--rm", "--network=host",
            "-v", "/root/.hermes:/root/.hermes",
            "legion-dashboard-app:latest",
            "/usr/local/lib/hermes-agent/venv/bin/hermes", "kanban", "show", "--json",
            builder_task.hermes_task_id,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                hermes_data = json.loads(result.stdout)
            else:
                return builder_task  # Keep existing data
        except Exception:
            return builder_task  # Keep existing data
    
    # Update local record with all available Hermes task fields
    # Bridge returns: {"task": {"task": {...}, "latest_summary": ..., "events": ..., "runs": ...}}
    # The inner "task" object contains the actual task fields (id, status, assignee, etc.)
    outer_task = hermes_data.get("task", hermes_data)
    if isinstance(outer_task, dict):
        # Check if this is the nested structure from the bridge
        if "task" in outer_task and isinstance(outer_task["task"], dict):
            task = outer_task["task"]  # Inner task object with status, assignee, etc.
        else:
            task = outer_task
    
    if isinstance(task, dict):
        builder_task.hermes_status = task.get("status", builder_task.hermes_status)
        builder_task.last_known_hermes_status = task.get("status")
        builder_task.hermes_assignee = task.get("assignee", builder_task.hermes_assignee)
        builder_task.hermes_result = task.get("result")
        builder_task.branch_name = task.get("branch_name")
        builder_task.last_sync_at = datetime.utcnow()
        
        # Extract PR URL / commit from comments or result
        comments = hermes_data.get("comments", [])
        if comments:
            # Look for PR/branch info in comments
            for comment in reversed(comments):
                comment_text = comment.get("body", "") if isinstance(comment, dict) else str(comment)
                if "github.com" in comment_text and "/pull/" in comment_text:
                    # Extract PR URL
                    import re
                    pr_match = re.search(r'https://github\.com/[^\s]+/pull/\d+', comment_text)
                    if pr_match:
                        builder_task.pr_url = pr_match.group(0)
                        break
        
        # Check for completed status
        if task.get("status") == "done":
            builder_task.completed_at = datetime.utcnow()

    db.commit()
    db.refresh(builder_task)

    # Release the repo lock when the Hermes task reaches a terminal state.
    # ``done`` -> success; ``failed``/``cancelled``/``aborted``/``archived``
    # -> terminal failure. The lock is keyed by repo path, so we only need
    # to know which terminal status we're handling.
    terminal_release = {
        "done": ("released", "build_completed"),
        "failed": ("failed", "build_failed"),
        "cancelled": ("aborted", "build_cancelled"),
        "aborted": ("aborted", "build_aborted"),
        "archived": ("released", "build_archived"),
    }
    if builder_task.target_repo and builder_task.hermes_status in terminal_release:
        final_status, reason = terminal_release[builder_task.hermes_status]
        # Pass the Hermes task_id so we only release the lock if it's still
        # owned by *this* builder task. If a newer build already took the
        # lock for the same repo, leave that newer lock alone.
        repo_safety.release_repo_lock(
            db,
            builder_task.target_repo,  # type: ignore[arg-type]
            release_reason=reason,
            final_status=final_status,
            expected_task_id=(
                str(builder_task.hermes_task_id)
                if builder_task.hermes_task_id is not None
                else None
            ),
        )

    # Project Hermes status to Work Item status (reusable lifecycle transition)
    sync_work_item_status_from_builder(
        db,
        builder_task.work_item_id,  # type: ignore[arg-type]
        builder_task.hermes_status,  # type: ignore[arg-type]
    )

    return builder_task
