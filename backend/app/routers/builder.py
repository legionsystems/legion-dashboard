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
from ..models import BuilderTask, RepoLock, WorkItem
from .. import repo_safety
from ..schemas_builder import BuilderTaskResponse, SendToBuilderRequest

router = APIRouter(prefix="/api/builder", tags=["builder"])


def _generate_hermes_prompt(work_item: WorkItem, debate_run_id: Optional[int] = None, recommendation: Optional[str] = None, implementation_readiness: Optional[str] = None, mandatory_edits_json: Optional[str] = None) -> str:
    """Generate Hermes Kanban task body/prompt."""
    # Parse mandatory edits if present
    mandatory_edits_str = ""
    if mandatory_edits_json:
        try:
            edits = json.loads(mandatory_edits_json)
            if isinstance(edits, list) and len(edits) > 0:
                mandatory_edits_str = "\n\nMANDATORY EDITS:\n" + "\n".join([f"- {e.get('field', 'Field')}: {e.get('required_change', 'Change required')}" for e in edits])
        except Exception:
            pass
    
    # Derive target repo from work item type/app
    target_repo = "/srv/repo/legion-dashboard"
    if work_item.target_app:
        if "hub" in work_item.target_app.lower():
            target_repo = "/srv/repo/lgn-hub"
        elif "dashboard" in work_item.target_app.lower():
            target_repo = "/srv/repo/legion-dashboard"
    
    prompt = f"""PROMPT ID: LEGION-IMPLEMENT-WI-{work_item.id}-CARD-001
PROMPT TYPE: approved-implementation-merge-deploy
TARGET HOST: lgn-remote-01
TARGET REPO: {target_repo}
TARGET PR: new PR to main
TASK: {work_item.title}
EXPECTED OUTCOME: Implement approved work item according to debate outcome{mandatory_edits_str}

SAFETY GATE — READ FIRST

You are working on lgn-remote-01.

Before doing any repo, git, Docker, or file mutation:
1. Verify hostname is lgn-remote-01.
2. Verify repo path is {target_repo}.
3. Verify current branch and git status.
4. Preserve dirty work.
5. Do not modify Hermes source/config.
6. Do not modify Ollama hosts, ai-4080, LEGION, or model runtime configuration.
7. Do not expose API keys, provider secrets, prompts, private work item data, raw model prompts, or raw attachment contents in logs.
8. Do not hard-delete any history.
9. Do not auto-approve or auto-implement without certification.

WORK ITEM DETAILS

- ID: {work_item.id}
- Type: {work_item.type}
- Priority: {work_item.priority}
- Target App: {work_item.target_app or "N/A"}
- Source: {work_item.source}
- Acceptance Notes: {work_item.acceptance_notes or "None provided"}

DEBATE OUTCOME

- Recommendation: {recommendation or "Not recorded"}
- Implementation Readiness: {implementation_readiness or "Not recorded"}
- Debate Run ID: {debate_run_id or "Not recorded"}

OUT OF SCOPE

- Do not implement Planning Chat, Discord notifications, attachments, debate display modes, clear/reset debates, Re-run Arbiter, operator-argument stance auto-assign, model/provider settings, or unrelated debate repair work.
- Do not use direct GitHub API orchestration.
- Do not create external/public demo or staging deployments unless already part of the existing Hermes implementation flow.

PHASE 1 — INSPECT

Run:
hostname
cd {target_repo} || exit 1
git status --short
git branch --show-current
git log -60 --oneline --decorate

Preserve dirty work before making changes.

PHASE 2 — IMPLEMENT

Implement the approved scope according to:
- Work item title and description
- Acceptance notes
- Mandatory edits (if APPROVE_WITH_MANDATORY_EDITS)
- Original scope (if APPROVE_AS_IS)

Do not downgrade or skip mandatory edits.

PHASE 3 — TESTS

Run:
- git diff --check
- backend tests
- frontend build/test if present
- docker compose config

PHASE 4 — INDEPENDENT REVIEW

Reviewer must use independent route from Builder.
Do not certify if implementation and review used same backend/model/profile.

PHASE 5 — COMMIT / MERGE / DEPLOY

If validation passes and review is CERTIFIED:
1. Commit with descriptive message
2. Push feature branch
3. Open/update PR to main
4. Merge automatically if clean
5. Sync main
6. Deploy if applicable
7. Runtime verify

PHASE 6 — REPORT

Write final report to /root/.hermes/LEGION_TOOLS/ with implementation provenance.

FINAL RESPONSE

Return only the clean LEGION TASK RESULT block.
"""
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


def _generate_review_prompt(
    work_item: WorkItem,
    builder_task_id: str,
    target_repo: str,
    pr_number: Optional[int] = None,
    branch_name: Optional[str] = None,
) -> str:
    """Generate the Codex review wrapper prompt for a review task.

    The review task body instructs the reviewer profile to orchestrate a
    Codex CLI review. The reviewer must not perform an LLM-only review;
    Codex must produce the verdict. If Codex tooling fails, the task is
    blocked with a tooling error.

    Args:
        work_item: The WorkItem being reviewed.
        builder_task_id: The Hermes task ID of the builder task.
        target_repo: Absolute path to the git repo on the target host.
        pr_number: PR number under review (optional).
        branch_name: Head branch that was built (optional).

    Returns:
        The full review task body string.
    """
    pr_ref = f"PR #{pr_number}" if pr_number else "the implementation branch"
    branch_ref = branch_name or "the feature branch"
    report_path = (
        f"/root/.hermes/LEGION_TOOLS/"
        f"review-report-wi-{work_item.id}-builder-{builder_task_id}.md"
    )

    prompt = f"""PROMPT ID: LEGION-REVIEW-WI-{work_item.id}-CARD-001
PROMPT TYPE: codex-required-review
ASSIGNEE ROLE: orchestrator (do not perform LLM-only review)
BUILDER TASK: {builder_task_id}
WORK ITEM: WI-{work_item.id}
TARGET REPO: {target_repo}
TARGET PR: {pr_number or "N/A"}
BASE BRANCH: main
HEAD BRANCH: {branch_ref}
EXPECTED REPORT: {report_path}
REVIEW VERDICT: PENDING

SAFETY GATE — READ FIRST

1. You are a review orchestrator. You MUST use the Codex CLI review
   wrapper to perform this review. Do NOT fall back to LLM-only review.
2. If Codex CLI is not available or fails, block this task with a tooling
   error. Do not proceed with a manual review.
3. Do not modify any files. This is read-only review.
4. Do not expose API keys, provider secrets, or private data in logs.
5. Record Codex verdict, report path, model/tool provenance, and reviewed
   commit on this task before completing.

ACCEPTANCE CRITERIA

- Builder-created review tasks explicitly require the Codex review wrapper.
- Review task body includes repo path, PR number, base branch, head branch,
  and expected report path.
- Reviewer profile is only used to orchestrate the review, not to replace
  Codex.
- Review cannot certify an implementation unless Codex returns APPROVE or
  APPROVE_WITH_NON_BLOCKING_NOTES.
- Codex verdict, report path, model/tool provenance, and reviewed commit are
  recorded on the review task.
- If Codex returns mandatory edits, needs rework, or blocked, the review
  task requests changes and does not certify.
- If Codex tooling fails, the task is blocked with a tooling error rather
  than falling back to LLM-only review.

REVIEW WORKFLOW

PHASE 1 — PREPARE
- Verify Codex CLI is available on the target host.
- Fetch the PR diff: git diff main...{branch_ref}
- Identify changed files, scope, and potential risk areas.

PHASE 2 — CODEX REVIEW
- Invoke Codex CLI review wrapper against {target_repo}.
- Pass the PR diff, changed files, and work item context to Codex.
- Codex must produce a structured verdict: APPROVE,
  APPROVE_WITH_NON_BLOCKING_NOTES, MANDATORY_EDITS, NEEDS_REWORK, or
  BLOCKED.

PHASE 3 — RECORD
- Write review report to {report_path} with:
  - Codex verdict
  - Codex model/tool provenance
  - Reviewed commit SHA
  - Findings (blocking and non-blocking)
  - Mandatory edits (if any)
- Record the verdict, report path, provenance, and commit on this Kanban
  task's result/comments.

PHASE 4 — CERTIFY OR REQUEST CHANGES
- If Codex returns APPROVE: mark this task complete with certification.
- If Codex returns APPROVE_WITH_NON_BLOCKING_NOTES: mark complete with
  notes for the operator.
- If Codex returns MANDATORY_EDITS, NEEDS_REWORK, or BLOCKED: mark this
  task as changes requested. Do NOT certify.
- If Codex tooling fails: BLOCK this task with reason
  "codex_tooling_failed: <error details>". Do NOT fall back to LLM-only.

OUT OF SCOPE
- Do not merge, push, or create PRs.
- Do not implement any changes.
- Do not modify Hermes source/config.
- Do not certify based on LLM-only review.

WORK ITEM CONTEXT
- ID: {work_item.id}
- Title: {work_item.title}
- Type: {work_item.type}
- Priority: {work_item.priority}
- Acceptance Notes: {work_item.acceptance_notes or "None provided"}

FINAL RESPONSE
Return only the clean LEGION TASK RESULT block.
"""
    return prompt


def _is_reviewer_tool_capable(reviewer_profile: Optional[str]) -> bool:
    """Validate that the reviewer profile is tool-capable.

    The reviewer profile must be able to orchestrate Codex CLI. This means
    the profile must exist and have access to the Codex CLI tools. For now,
    we validate that a reviewer profile is explicitly set (not None and not
    'default') and that it is one of the known tool-capable profiles.

    Args:
        reviewer_profile: The Hermes profile name for the reviewer.

    Returns:
        True if the reviewer profile is tool-capable.

    Raises:
        HTTPException: If the reviewer profile is not set or not tool-capable.
    """
    if not reviewer_profile:
        raise HTTPException(
            status_code=400,
            detail=(
                "Review task creation requires a tool-capable reviewer profile. "
                "No reviewer_profile is set on the work item."
            ),
        )
    # Profiles that are known to support Codex CLI orchestration.
    # The 'reviewer' profile is the dedicated review orchestrator.
    # The 'builder' profile is technically capable but should not be used
    # for review (same-model blocking is enforced elsewhere).
    _TOOL_CAPABLE_REVIEWER_PROFILES = frozenset({"reviewer"})
    if reviewer_profile not in _TOOL_CAPABLE_REVIEWER_PROFILES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Reviewer profile '{reviewer_profile}' is not tool-capable for "
                "Codex review orchestration. Set reviewer_profile to 'reviewer'."
            ),
        )
    return True


def _create_review_task(
    db: Session,
    work_item: WorkItem,
    builder_task: BuilderTask,
    target_repo: str,
) -> Optional[str]:
    """Create a Hermes Kanban review task with Codex review wrapper prompt.

    Called immediately after the builder task is created. The review task
    is assigned to the reviewer profile (from work_item.reviewer_profile)
    and is placed in Ready status (not Todo).

    The review task body (generated by ``_generate_review_prompt``) includes:
    - Repo path, PR number, base branch, head branch, expected report path
    - Explicit requirement for the Codex review wrapper
    - Instructions to block on tooling failure rather than fall back to LLM-only

    Args:
        db: Database session.
        work_item: The WorkItem being built.
        builder_task: The just-created BuilderTask ORM instance (already
            persisted with an ID and hermes_task_id).
        target_repo: Absolute path to the git repo on the target host.

    Returns:
        The Hermes task ID of the created review task, or None if no
        reviewer profile is configured on the work item (review deferred).

    Raises:
        HTTPException: If the reviewer profile is set but not tool-capable,
            or if the Hermes bridge call fails.
    """
    reviewer_profile = work_item.reviewer_profile

    # If no reviewer profile is set, skip review task creation.
    # The operator can create a review task manually later.
    if not reviewer_profile:
        return None

    # Validate the reviewer profile is tool-capable.
    _is_reviewer_tool_capable(reviewer_profile)

    # Generate the Codex review wrapper prompt.
    review_body = _generate_review_prompt(
        work_item=work_item,
        builder_task_id=builder_task.hermes_task_id,
        target_repo=target_repo,
        pr_number=work_item.pr_number,
        branch_name=work_item.branch_name or builder_task.branch_name,
    )

    # Create the Hermes review task in Ready status (not Todo).
    review_title = f"REVIEW-WI-{work_item.id} — {work_item.title[:80]}"
    review_idem_key = (
        f"legion-dashboard-wi-{work_item.id}-"
        f"review-{builder_task.hermes_task_id}-v1"
    )

    review_result = _create_hermes_task(
        title=review_title,
        body=review_body,
        assignee=reviewer_profile,
        idempotency_key=review_idem_key,
        priority=work_item.priority,
        # No status_override — review tasks go to Ready by default.
    )

    review_task_id = review_result.get("task_id")
    if not review_task_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Hermes review task created but no task_id returned",
        )

    return review_task_id


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
        debate_run_id=latest_debate.id if latest_debate else None,
        recommendation=latest_debate.final_recommendation if latest_debate else None,
        implementation_readiness=latest_debate.implementation_readiness if latest_debate else None,
        mandatory_edits_json=latest_debate.summary if latest_debate else None,
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
        mandatory_edits_json=latest_debate.summary if latest_debate else None,
        generated_prompt_snapshot=prompt_body,
    )

    db.add(builder_task)
    db.commit()
    db.refresh(builder_task)

    # Backfill the lock's task_id now that we have the Hermes ID.
    if acquired_lock is not None and hermes_result.get("task_id"):
        acquired_lock.task_id = str(hermes_result["task_id"])
        db.commit()

    # ------------------------------------------------------------------
    # Create the Codex review task (if reviewer profile is configured).
    # The review task is created in Ready status and references this
    # builder task. It is NOT parent/child-linked to the builder task
    # so it can be claimed independently.
    #
    # CRITICAL: Review tasks are ONLY created for the implementation
    # path (start-build / status_override="ready"). The triage path
    # (send-to-builder / status_override="triage") queues a Hermes card
    # for later review but does NOT start a build or create a review task.
    # Creating a review task during triage would allow reviewers to claim
    # a review before there is any implementation or PR to review.
    #
    # If the work item has a reviewer_profile set AND this is the
    # implementation path, the review task is REQUIRED. If review task
    # creation fails, Start Build must fail closed: clean up the builder
    # task and repo lock, then raise.
    #
    # If the reviewer profile is not set, review task creation is
    # skipped (operator can create manually later).
    # ------------------------------------------------------------------
    is_implementation_path = status_override != "triage"
    reviewer_profile = work_item.reviewer_profile
    review_task_required = reviewer_profile is not None and is_implementation_path
    review_task_id = None

    if review_task_required:
        try:
            review_task_id = _create_review_task(
                db=db,
                work_item=work_item,
                builder_task=builder_task,
                target_repo=target_repo,
            )
        except Exception:
            # Review task creation failed when it was REQUIRED.
            # Clean up: delete the builder task, release the repo lock,
            # then raise so Start Build fails clearly.
            db.rollback()

            # Delete the builder task we just created.
            db.delete(builder_task)
            db.commit()

            # Release the repo lock if we acquired one.
            if acquired_lock is not None:
                repo_safety.release_repo_lock(
                    db,
                    target_repo,
                    release_reason="review_task_create_failed",
                    final_status="failed",
                )

            raise HTTPException(
                status_code=502,
                detail={
                    "blocker_code": "review_task_create_failed",
                    "blocker_message": (
                        f"Codex review task creation failed for reviewer profile "
                        f"'{reviewer_profile}'. Start Build cannot proceed without "
                        f"the required review task. Check Hermes bridge connectivity "
                        f"and reviewer profile configuration."
                    ),
                    "work_item_id": work_item_id,
                    "reviewer_profile": reviewer_profile,
                },
            )

    # Review task creation succeeded (or was not required).
    if review_task_id is not None:
        builder_task.review_task_id = review_task_id
        db.commit()
        db.refresh(builder_task)

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
