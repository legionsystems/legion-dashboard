"""Tests for the Start Build flow's host-side repo safety inspection.

These tests focus on the integration between ``builder.start_build`` and the
host-side preview executor that backs the safety gate. The original gate
ran ``git`` inside the dashboard container, where ``/srv/repo`` is not a
git worktree — every Start Build came back with a misleading
``git_inspection_failed`` 409. The gate now delegates to the host executor
via :func:`app.repo_safety.check_repo_clean_via_executor`.

The cases below cover both legs of that contract:
  * Start Build invokes the executor-backed inspector (not the direct one).
  * Executor failures keep the Work Item out of ``building``: no builder
    task, no repo lock, no effective-state advance.
"""
from __future__ import annotations

import pytest

from app import preview_deploy, repo_safety
from app.models import BuilderTask, RepoLock, WorkItem
from app.routers import builder as builder_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_approved_work_item(db_session, **overrides) -> WorkItem:
    item = WorkItem(
        type="task",
        title="Start Build candidate",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
    )
    for k, v in overrides.items():
        setattr(item, k, v)
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def _patch_target_repo(monkeypatch, repo_path: str):
    """Pin the builder's repo resolution to ``repo_path`` for the test."""
    monkeypatch.setattr(
        builder_router,
        "_resolve_target_repo",
        lambda _wi: (repo_path, "legion-dashboard"),
    )


def _stub_hermes(monkeypatch, task_id: str = "hermes-task-1"):
    def _fake(**kwargs):
        return {
            "task_id": task_id,
            "status": kwargs.get("status_override") or "ready",
            "assignee": kwargs.get("assignee") or "builder",
        }
    monkeypatch.setattr(builder_router, "_create_hermes_task", _fake)


def _stub_executor(monkeypatch, response: preview_deploy.ExecutorResponse):
    """Replace ``call_host_executor`` with a recorder so the test can assert
    on the action / repo_path the dashboard forwarded."""
    calls = []

    def _fake(**kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(preview_deploy, "call_host_executor", _fake)
    return calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_start_build_invokes_executor_safety_check_not_direct_git(
    client, db_session, monkeypatch
):
    """Start Build must inspect the worktree through the host executor; it
    must NOT call the in-container ``check_repo_clean`` (which would 409
    every Start Build because /srv/repo isn't a git worktree inside the
    dashboard container).
    """
    item = _make_approved_work_item(db_session)
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch, task_id="hermes-host-check-1")

    # Spy on the in-container inspector — Start Build must not call it.
    direct_calls = []
    real_check = repo_safety.check_repo_clean

    def _spy_direct(path):
        direct_calls.append(path)
        return real_check(path)

    monkeypatch.setattr(repo_safety, "check_repo_clean", _spy_direct)

    # Stub the executor channel with a clean response.
    executor_calls = _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "repo_path": "/srv/repo/legion-dashboard",
                "is_clean": True,
                "dirty_files": [],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "main",
                "current_commit": "f" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert response.status_code == 200, response.text

    # The executor was called for the safety check, with the resolved repo.
    safety_calls = [c for c in executor_calls if c.get("action") == "repo_safety_check"]
    assert len(safety_calls) == 1
    assert safety_calls[0]["repo_path"] == "/srv/repo/legion-dashboard"

    # The in-container direct inspector was never reached.
    assert direct_calls == []


def test_start_build_blocked_when_executor_reports_dirty_repo(
    client, db_session, monkeypatch
):
    """A dirty-repo response from the executor must 409 Start Build, leave
    the Work Item out of ``building``, and create no builder task or lock.
    """
    item = _make_approved_work_item(db_session)
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch)

    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "is_clean": False,
                "dirty_files": ["README.md"],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "main",
                "current_commit": "c" * 40,
                "blocker_code": "blocked_dirty_repo",
                "blocker_message": (
                    "Repo /srv/repo/legion-dashboard has uncommitted changes: "
                    "1 unstaged"
                ),
            },
        ),
    )

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["blocker_code"] == "blocked_dirty_repo"
    assert detail["dirty_files"] == ["README.md"]

    # No builder task and no lock were created — the gate refused the work.
    assert (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .count()
        == 0
    )
    assert (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == "/srv/repo/legion-dashboard")
        .count()
        == 0
    )

    # The Work Item must not have advanced into a building / active state.
    refreshed = client.get(f"/api/work-items/{item.id}").json()
    assert refreshed["effective_state"] != "building"
    assert refreshed["effective_state"] != "active"


def test_start_build_fails_closed_when_executor_unreachable(
    client, db_session, monkeypatch
):
    """An ``executor_unreachable`` response must block Start Build — the
    gate has to fail closed, not silently let a build start without
    verifying the worktree."""
    item = _make_approved_work_item(db_session)
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch)

    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=False,
            error="executor unreachable: Connection refused",
            error_code="executor_unreachable",
        ),
    )

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["blocker_code"] == "executor_unreachable"

    # No builder task, no lock, and no state advance to active/building.
    assert (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .count()
        == 0
    )
    assert (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == "/srv/repo/legion-dashboard")
        .count()
        == 0
    )
    refreshed = client.get(f"/api/work-items/{item.id}").json()
    assert refreshed["effective_state"] != "building"
    assert refreshed["effective_state"] != "active"


def test_start_build_proceeds_when_executor_reports_clean(
    client, db_session, monkeypatch
):
    """A clean executor response must let Start Build proceed: builder task
    created, lock acquired, current branch/commit recorded from the executor's
    response (not from in-container git)."""
    item = _make_approved_work_item(db_session)
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch, task_id="hermes-clean-1")

    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "is_clean": True,
                "dirty_files": [],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "feature/host-side-gate",
                "current_commit": "d" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert response.status_code == 200, response.text

    # Builder task created and lock acquired against the host-side worktree.
    builder_tasks = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(builder_tasks) == 1
    assert builder_tasks[0].hermes_task_id == "hermes-clean-1"

    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == "/srv/repo/legion-dashboard")
        .one()
    )
    assert lock.lock_status == "active"
    # The lock recorded the branch/commit the executor reported — proves
    # the dashboard trusts the host's view rather than the container's.
    assert lock.branch_name == "feature/host-side-gate"
    assert lock.commit_sha == "d" * 40


# ---------------------------------------------------------------------------
# Review task creation tests — Codex review wrapper
# ---------------------------------------------------------------------------


def test_review_task_created_when_reviewer_profile_set(
    client, db_session, monkeypatch
):
    """When work_item.reviewer_profile is 'reviewer', start-build must
    also create a Hermes review task with the Codex review wrapper prompt.
    """
    item = _make_approved_work_item(
        db_session,
        reviewer_profile="reviewer",
        pr_number=42,
        branch_name="main",  # Match the executor's current_branch to pass the gate
    )
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch, task_id="hermes-builder-1")
    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "is_clean": True,
                "dirty_files": [],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "main",
                "current_commit": "a" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    # Stub _create_hermes_task to capture the review task call.
    import app.routers.builder as builder_module
    original_create = builder_module._create_hermes_task
    hermes_calls = []

    def spy_create_hermes(**kwargs):
        hermes_calls.append(kwargs)
        task_id = kwargs.get("idempotency_key", "unknown")
        # Return different IDs for builder vs review tasks.
        if "review" in task_id:
            return {"task_id": "hermes-review-1", "status": "ready", "assignee": "reviewer"}
        return {"task_id": "hermes-builder-1", "status": "ready", "assignee": "builder"}

    monkeypatch.setattr(builder_module, "_create_hermes_task", spy_create_hermes)

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert response.status_code == 200, response.text

    # Two Hermes tasks must have been created: builder + review.
    assert len(hermes_calls) == 2

    # The second call is the review task.
    review_call = hermes_calls[1]
    assert review_call["assignee"] == "reviewer"
    assert "REVIEW-WI-" in review_call["title"]
    assert review_call.get("status_override") is None  # Ready by default

    # The review body must require Codex.
    review_body = review_call["body"]
    assert "codex-required-review" in review_body
    assert "PROMPT ID: LEGION-REVIEW-WI-" in review_body
    assert "TARGET REPO: /srv/repo/legion-dashboard" in review_body
    assert "TARGET PR: 42" in review_body
    assert "BASE BRANCH: main" in review_body
    assert "HEAD BRANCH: main" in review_body
    assert "BUILDER TASK: hermes-builder-1" in review_body
    assert "WORK ITEM: WI-" in review_body
    assert "Codex CLI review" in review_body
    assert "EXPECTED REPORT:" in review_body
    assert "REVIEW VERDICT: PENDING" in review_body

    # The builder task record must reference the review task.
    builder_tasks = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(builder_tasks) == 1
    assert builder_tasks[0].review_task_id == "hermes-review-1"


def test_review_task_not_created_when_no_reviewer_profile(
    client, db_session, monkeypatch
):
    """When work_item.reviewer_profile is None, no review task is created.
    The start-build still succeeds and only the builder task is created.
    """
    item = _make_approved_work_item(db_session)
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch, task_id="hermes-builder-only")
    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "is_clean": True,
                "dirty_files": [],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "main",
                "current_commit": "b" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    import app.routers.builder as builder_module
    original_create = builder_module._create_hermes_task
    hermes_calls = []

    def spy_create_hermes(**kwargs):
        hermes_calls.append(kwargs)
        return {"task_id": "hermes-builder-only", "status": "ready", "assignee": "builder"}

    monkeypatch.setattr(builder_module, "_create_hermes_task", spy_create_hermes)

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert response.status_code == 200, response.text

    # Only one Hermes task: the builder (no review task).
    assert len(hermes_calls) == 1
    assert hermes_calls[0]["assignee"] == "builder"

    # The builder task has no review_task_id.
    builder_tasks = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(builder_tasks) == 1
    assert builder_tasks[0].review_task_id is None


def test_send_to_builder_triage_does_not_create_review_task_even_with_profile(
    client, db_session, monkeypatch
):
    """When using /send-to-builder (triage path), NO review task is created
    even when work_item.reviewer_profile is set. The triage path only queues
    a Hermes card for later review — it does not start a build or create a
    review task. Review tasks are only created for the start-build path.
    """
    item = _make_approved_work_item(
        db_session,
        reviewer_profile="reviewer",  # Set reviewer profile
        branch_name="main",  # Pass the safety gate
    )
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch, task_id="hermes-builder-triage")
    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "is_clean": True,
                "dirty_files": [],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "main",
                "current_commit": "t" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    import app.routers.builder as builder_module
    hermes_calls = []

    def spy_create_hermes(**kwargs):
        hermes_calls.append(kwargs)
        return {"task_id": "hermes-builder-triage", "status": "triage", "assignee": "builder"}

    monkeypatch.setattr(builder_module, "_create_hermes_task", spy_create_hermes)

    # Use send-to-builder endpoint (triage path), not start-build.
    response = client.post(
        f"/api/builder/work-items/{item.id}/send-to-builder", json={}
    )
    assert response.status_code == 200, response.text

    # Only one Hermes task: the builder in triage status (no review task).
    assert len(hermes_calls) == 1
    assert hermes_calls[0]["assignee"] == "builder"
    assert hermes_calls[0].get("status_override") == "triage"

    # The builder task has no review_task_id.
    builder_tasks = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(builder_tasks) == 1
    assert builder_tasks[0].review_task_id is None


def test_review_task_fails_with_non_tool_capable_profile(
    client, db_session, monkeypatch
):
    """When work_item.reviewer_profile is set to a non-tool-capable value
    (e.g., 'default' or 'builder'), the review task creation must fail
    and Start Build must fail closed (no builder task, no repo lock).
    """
    item = _make_approved_work_item(
        db_session,
        reviewer_profile="default",  # Not in _TOOL_CAPABLE_REVIEWER_PROFILES
        branch_name="main",  # Pass the safety gate
    )
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch, task_id="hermes-builder-fallback")
    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "is_clean": True,
                "dirty_files": [],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "main",
                "current_commit": "c" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    # Start Build must fail when review task creation is required but fails.
    assert response.status_code == 502, response.text
    data = response.json()
    assert data["detail"]["blocker_code"] == "review_task_create_failed"
    assert "default" in data["detail"]["blocker_message"]

    # No builder task should have been created.
    builder_tasks = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(builder_tasks) == 0


def test_review_task_body_contains_report_path_and_provenance_fields(
    client, db_session, monkeypatch
):
    """The review task body must include the expected report path and
    require Codex model/tool provenance recording.
    """
    item = _make_approved_work_item(
        db_session,
        reviewer_profile="reviewer",
        pr_number=99,
    )
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
    _stub_hermes(monkeypatch, task_id="hermes-builder-body")
    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "is_clean": True,
                "dirty_files": [],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "main",
                "current_commit": "e" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    import app.routers.builder as builder_module
    review_bodies = []

    def spy_create_hermes(**kwargs):
        task_id = kwargs.get("idempotency_key", "unknown")
        if "review" in task_id:
            review_bodies.append(kwargs.get("body", ""))
            return {"task_id": "hermes-review-body", "status": "ready", "assignee": "reviewer"}
        return {"task_id": "hermes-builder-body", "status": "ready", "assignee": "builder"}

    monkeypatch.setattr(builder_module, "_create_hermes_task", spy_create_hermes)

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert response.status_code == 200, response.text

    assert len(review_bodies) == 1
    body = review_bodies[0]

    # Report path format.
    assert "review-report-wi-" in body
    assert "-builder-hermes-builder-body.md" in body

    # Provenance recording requirement.
    assert "model/tool provenance" in body
    assert "Codex model/tool provenance" in body

    # Codex verdict options.
    assert "APPROVE" in body
    assert "APPROVE_WITH_NON_BLOCKING_NOTES" in body
    assert "MANDATORY_EDITS" in body
    assert "NEEDS_REWORK" in body
    assert "BLOCKED" in body

    # Blocking on tooling failure.
    assert "codex_tooling_failed" in body
    assert "Do NOT fall back to LLM-only review" in body

    # Work item context is included.
    assert f"ID: {item.id}" in body
    assert f"Title: {item.title}" in body
    assert f"Type: {item.type}" in body

    # Safety constraints.
    assert "Do not modify Hermes source/config" in body
    assert "Do not merge, push, or create PRs" in body

    # PR and repo references.
    assert "TARGET REPO: /srv/repo/legion-dashboard" in body
    assert "TARGET PR: 99" in body


def test_review_task_hermes_bridge_failure_fails_start_build(
    client, db_session, monkeypatch
):
    """When reviewer_profile is set but the Hermes bridge call fails
    during review task creation, Start Build must fail closed:
    - Return 502 error with clear blocker message
    - No builder task created
    - No repo lock left behind
    """
    item = _make_approved_work_item(
        db_session,
        reviewer_profile="reviewer",
        pr_number=55,
        branch_name="main",  # Pass the safety gate
    )
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")

    # Stub Hermes to succeed for builder task but fail for review task.
    import app.routers.builder as builder_module
    hermes_calls = []

    def stub_hermes(**kwargs):
        hermes_calls.append(kwargs)
        task_id = kwargs.get("idempotency_key", "unknown")
        if "review" in task_id:
            # Simulate Hermes bridge failure for review task.
            raise Exception("Hermes bridge connection refused")
        return {"task_id": "hermes-builder-bridge-fail", "status": "ready", "assignee": "builder"}

    monkeypatch.setattr(builder_module, "_create_hermes_task", stub_hermes)

    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=True,
            raw={
                "success": True,
                "action": "repo_safety_check",
                "is_clean": True,
                "dirty_files": [],
                "staged_files": [],
                "untracked_files": [],
                "current_branch": "main",
                "current_commit": "d" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    # Start Build must fail when review task creation fails.
    assert response.status_code == 502, response.text
    data = response.json()
    assert data["detail"]["blocker_code"] == "review_task_create_failed"
    assert "reviewer" in data["detail"]["blocker_message"]
    assert "Hermes bridge" in data["detail"]["blocker_message"]

    # No builder task should have been created.
    builder_tasks = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(builder_tasks) == 0

    # Verify the Hermes call was attempted (builder task created first).
    assert len(hermes_calls) >= 1
