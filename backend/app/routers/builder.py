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


def _generate_hermes_prompt(
    work_item: WorkItem,
    db: Optional[Session] = None,
    debate_run_id: Optional[int] = None,
    recommendation: Optional[str] = None,
    implementation_readiness: Optional[str] = None,
    mandatory_edits: Optional[list] = None,
) -> str:
    """Generate the Hermes Kanban implementation card body.

    The canonical assembly lives in :mod:`app.builder_card`. This
    adapter exists so callers that previously passed
    ``mandatory_edits_json`` still work, and so the prompt is built
    from the structured source (the Final Arbiter's JSON, parsed from
    the latest ``DebateArgument``) rather than the previous misuse of
    ``DebateRun.summary`` (which stores the rationale text).
    """
    target_repo = _resolve_target_repo_path(work_item)

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
    )
    return prompt


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

    # ------------------------------------------------------------------
    # Repo safety gate (workflow slice 3): refuse to start a build when
    # the target repo has uncommitted changes or is already locked by
    # another in-flight build.
    # ------------------------------------------------------------------
    target_repo, repo_name = _resolve_target_repo(work_item)
    skip_gate = os.environ.get("LEGION_SKIP_REPO_SAFETY_GATE") == "1"
    # Triage-only sends queue a Hermes card without starting a build, so they
    # must not lock the repo (or be blocked by an in-flight build's lock).
    # We still run the dirty/branch checks so an operator sees the same gate
    # feedback whether they triage or start immediately.
    skip_lock = status_override == "triage"
    acquired_lock: Optional[RepoLock] = None
    if not skip_gate:
        # Run the safety inspection on the host (via the preview executor)
        # rather than inside the container. The container's /srv/repo mount
        # is not a valid git worktree, so an in-container ``git status``
        # would fail with ``fatal: not a git repository`` and the gate
        # would 409 every Start Build with ``git_inspection_failed``. The
        # host executor inspects the real worktree and returns the same
        # RepoSafetyResult shape.
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

        # When the work item explicitly names a branch and the worktree is on
        # a different one, refuse to start so the builder doesn't push to the
        # wrong head.
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
                task_id=None,  # populated below after Hermes responds
                lock_owner=request.hermes_assignee or "builder",
            )
            if not lock_result.acquired:
                # Race lost between check_repo_busy and the INSERT. Treat the
                # same as busy.
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
    
    # Get latest completed debate run for this work item
    from ..models import DebateRun
    latest_debate = db.query(DebateRun).filter(
        DebateRun.work_item_id == work_item_id,
        DebateRun.status == "completed"
    ).order_by(DebateRun.completed_at.desc()).first()
    
    # Generate prompt
    prompt_body = _generate_hermes_prompt(
        work_item=work_item,
        db=db,
        debate_run_id=latest_debate.id if latest_debate else None,
        recommendation=latest_debate.final_recommendation if latest_debate else None,
        implementation_readiness=latest_debate.implementation_readiness if latest_debate else None,
    )
    
    # Create Hermes task. If this fails we must release the lock we just
    # acquired so the repo doesn't stay pinned to a build that never started.
    idempotency_key = f"legion-dashboard-work-item-{work_item_id}-builder-v1"
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
        raise

    # Create builder task record
    # mandatory_edits_json is now a real JSON list of mandatory edits
    # extracted from the Final Arbiter's structured output. It used to
    # be set to DebateRun.summary (which is the rationale text) — that
    # was a prompt-generation bug. The actual edits live on the latest
    # DebateArgument with side='arbiter'.
    mandatory_edits_for_record: list = []
    if latest_debate is not None:
        mandatory_edits_for_record = load_arbiter_mandatory_edits(
            db, latest_debate
        )
    builder_task = BuilderTask(
        work_item_id=work_item_id,
        hermes_task_id=hermes_result["task_id"],
        hermes_board="legion-apps-build-queue",
        hermes_status=hermes_result["status"],
        hermes_assignee=request.hermes_assignee or "builder",
        title=work_item.title,
        target_app=work_item.target_app,
        target_repo=target_repo,
        priority=request.priority or work_item.priority,
        debate_run_id=latest_debate.id if latest_debate else None,
        recommendation=latest_debate.final_recommendation if latest_debate else None,
        implementation_readiness=latest_debate.implementation_readiness if latest_debate else None,
        mandatory_edits_json=(
            json.dumps(mandatory_edits_for_record)
            if mandatory_edits_for_record
            else None
        ),
        generated_prompt_snapshot=prompt_body,
    )

    db.add(builder_task)
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
