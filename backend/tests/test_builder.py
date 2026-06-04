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

    # The executor was called for the safety check. After
    # worktree-isolation consolidation, the safety check inspects
    # the task worktree path (the checkout the builder will
    # operate in), not the shared operator/control repo.
    safety_calls = [c for c in executor_calls if c.get("action") == "repo_safety_check"]
    assert len(safety_calls) == 1
    assert safety_calls[0]["repo_path"] == (
        f"/srv/worktrees/legion-dashboard/wi-{item.id}-start-build-candidate/t_000001"
    )

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

    # Builder task created and lock acquired against the task
    # worktree (post-consolidation: the safety check and the
    # lock apply to the path the builder will operate in,
    # not the shared operator/control repo).
    builder_tasks = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(builder_tasks) == 1
    assert builder_tasks[0].hermes_task_id == "hermes-clean-1"

    lock = (
        db_session.query(RepoLock)
        .filter(
            RepoLock.repo_path
            == f"/srv/worktrees/legion-dashboard/wi-{item.id}-start-build-candidate/t_000001"
        )
        .one()
    )
    assert lock.lock_status == "active"
    # The lock recorded the branch/commit the executor reported for
    # the task worktree — proves the dashboard trusts the host's
    # view of the path the builder will operate in.
    assert lock.branch_name == "feature/host-side-gate"
    assert lock.commit_sha == "d" * 40
