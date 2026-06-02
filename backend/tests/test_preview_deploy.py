"""Tests for workflow slice 4 — preview deployment / revert actions.

Slice 4b moved the side-effectful work (git checkout, ``docker compose``)
to a host-side executor reachable over HTTP. These tests monkeypatch
``preview_deploy.call_host_executor`` so the router's state machine
(state gates, repo safety gate, lock acquire/release, metadata stamping
on confirmed success only) can be exercised without standing up the
executor. The repo-safety gate is still exercised against a real
temporary git repo so the dirty / busy paths match production behaviour.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app import preview_deploy
from app.models import RepoLock, WorkItem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def clean_repo(tmp_path):
    """Initialize a clean git repo with one commit on the default branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / ".gitignore").write_text(".hermes-config.yaml\n")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _make_review_ready_item(db, **overrides) -> WorkItem:
    """A Work Item in ``in_review`` effective state with branch + PR set."""
    fields = dict(
        type="task",
        title="Preview deploy candidate",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        branch_name="feature/wi-preview",
        pr_number=42,
        pr_url="https://example.invalid/pr/42",
    )
    fields.update(overrides)
    item = WorkItem(**fields)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def _executor_success(
    commit_sha: str = "a" * 40, health: str = "healthy"
) -> preview_deploy.ExecutorResponse:
    return preview_deploy.ExecutorResponse(
        success=True,
        commit_sha=commit_sha,
        health_status=health,
    )


def _executor_failure(
    error: str = "boom", error_code: str = "executor_compose_build_failed"
) -> preview_deploy.ExecutorResponse:
    return preview_deploy.ExecutorResponse(
        success=False,
        error=error,
        error_code=error_code,
    )


def _stub_executor(monkeypatch, response: preview_deploy.ExecutorResponse):
    """Monkeypatch ``call_host_executor`` to return ``response``.

    Also records each call so tests can assert on the arguments the router
    forwarded to the executor.
    """
    calls = []

    def _fake_call(**kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(preview_deploy, "call_host_executor", _fake_call)
    return calls


def _stub_repo_resolution(monkeypatch, repo_path: str):
    monkeypatch.setattr(
        preview_deploy,
        "resolve_preview_repo",
        lambda _wi: (repo_path, "legion-dashboard"),
    )


# ---------------------------------------------------------------------------
# Unit-level guards
# ---------------------------------------------------------------------------


def test_is_deploy_state_allowed_accepts_review_states():
    assert preview_deploy.is_deploy_state_allowed("in_review") is True
    assert preview_deploy.is_deploy_state_allowed("preview_pending") is True
    assert preview_deploy.is_deploy_state_allowed("preview_ready") is True
    assert preview_deploy.is_deploy_state_allowed("code_reviewed") is True
    assert preview_deploy.is_deploy_state_allowed("needs_rework") is True


def test_is_deploy_state_allowed_rejects_terminal_states():
    assert preview_deploy.is_deploy_state_allowed("merged") is False
    assert preview_deploy.is_deploy_state_allowed("implemented") is False
    assert preview_deploy.is_deploy_state_allowed("drafting") is False
    assert preview_deploy.is_deploy_state_allowed(None) is False


def test_is_preview_state_blocked_flags_terminal_states():
    assert preview_deploy.is_preview_state_blocked("merged") is True
    assert preview_deploy.is_preview_state_blocked("implemented") is True
    assert preview_deploy.is_preview_state_blocked("archived") is True
    assert preview_deploy.is_preview_state_blocked("rejected") is True
    assert preview_deploy.is_preview_state_blocked("in_review") is False


# ---------------------------------------------------------------------------
# deploy-preview endpoint — input validation
# ---------------------------------------------------------------------------


def test_deploy_requires_branch_metadata(client, db_session, monkeypatch, clean_repo):
    """Missing branch_name or pr_number must 400 — preview needs a PR head."""
    item = _make_review_ready_item(
        db_session, branch_name=None, pr_number=None
    )
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(monkeypatch, _executor_success())

    response = client.post(f"/api/work-items/{item.id}/deploy-preview", json={})

    assert response.status_code == 400
    assert "branch" in response.json()["detail"].lower()
    assert (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .count()
        == 0
    )


def test_deploy_blocks_merged_work_item(client, db_session, monkeypatch, clean_repo):
    """A merged Work Item (merge_commit_sha set) is a hard 409."""
    item = _make_review_ready_item(db_session, merge_commit_sha="d" * 40)
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(monkeypatch, _executor_success())

    response = client.post(f"/api/work-items/{item.id}/deploy-preview", json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "merged" in detail.lower()
    assert (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .count()
        == 0
    )
    db_session.refresh(item)
    assert item.preview_deployed in (None, False)
    assert item.preview_status is None


def test_deploy_blocks_certified_work_item(client, db_session, monkeypatch, clean_repo):
    """An operator-certified item is past the review window — 409."""
    from datetime import datetime as _dt

    item = _make_review_ready_item(
        db_session, operator_certified=True, certified_at=_dt.utcnow()
    )
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(monkeypatch, _executor_success())

    response = client.post(f"/api/work-items/{item.id}/deploy-preview", json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "certified" in detail.lower()


def test_deploy_blocks_archived_work_item(client, db_session, monkeypatch, clean_repo):
    """An archived item is dormant — 409 with the archived state label."""
    from datetime import datetime as _dt

    item = _make_review_ready_item(
        db_session, archived=True, archived_at=_dt.utcnow()
    )
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(monkeypatch, _executor_success())

    response = client.post(f"/api/work-items/{item.id}/deploy-preview", json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "archived" in detail.lower()


# ---------------------------------------------------------------------------
# deploy-preview endpoint — repo safety gate
# ---------------------------------------------------------------------------


def test_deploy_blocked_when_repo_dirty(client, db_session, monkeypatch, clean_repo):
    """A dirty worktree must 409 with the same shape as the builder gate."""
    item = _make_review_ready_item(db_session)
    (clean_repo / "README.md").write_text("dirty\n")

    _stub_repo_resolution(monkeypatch, str(clean_repo))
    calls = _stub_executor(monkeypatch, _executor_success())

    response = client.post(f"/api/work-items/{item.id}/deploy-preview", json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["blocker_code"] == "blocked_dirty_repo"
    assert "README.md" in detail["dirty_files"]
    # No executor call, no lock acquired on the dirty path.
    assert calls == []
    assert (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .count()
        == 0
    )
    db_session.refresh(item)
    assert item.preview_deployed in (None, False)


def test_deploy_blocked_when_repo_busy(client, db_session, monkeypatch, clean_repo):
    """An existing active lock blocks preview — same as the builder gate."""
    from app import repo_safety

    repo_safety.acquire_repo_lock(
        session=db_session,
        repo_path=str(clean_repo),
        repo_name="legion-dashboard",
        branch_name="main",
        commit_sha="a" * 40,
        task_id="prior-build",
        lock_owner="builder",
    )

    item = _make_review_ready_item(db_session)
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    calls = _stub_executor(monkeypatch, _executor_success())

    response = client.post(f"/api/work-items/{item.id}/deploy-preview", json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["blocker_code"] == "blocked_repo_busy"
    # The pre-existing lock is unchanged and the executor was never called.
    assert calls == []
    locks = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .all()
    )
    assert len(locks) == 1
    assert locks[0].task_id == "prior-build"
    assert locks[0].lock_status == "active"


# ---------------------------------------------------------------------------
# deploy-preview endpoint — executor success / failure paths
# ---------------------------------------------------------------------------


def test_successful_deploy_stamps_metadata_and_releases_lock(
    client, db_session, monkeypatch, clean_repo
):
    """Successful executor response → metadata stamped, lock released."""
    item = _make_review_ready_item(db_session)
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    branch_head_sha = "b" * 40
    calls = _stub_executor(
        monkeypatch, _executor_success(commit_sha=branch_head_sha)
    )

    response = client.post(
        f"/api/work-items/{item.id}/deploy-preview",
        json={"deployed_by": "operator-alice"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preview_status"] == "deployed"
    assert body["preview_deployed"] is True
    assert body["preview_branch"] == item.branch_name
    assert body["preview_pr_number"] == 42
    assert body["preview_health_status"] == "healthy"
    assert body["preview_deployed_by"] == "operator-alice"
    assert body["preview_deployed_at"] is not None
    assert body["preview_error"] is None
    # ``preview_commit_sha`` reflects the PR branch HEAD that the executor
    # returned, not the base branch HEAD captured at lock-acquire time.
    assert body["preview_commit_sha"] == branch_head_sha

    # Executor was called exactly once with the PR branch.
    assert len(calls) == 1
    call = calls[0]
    assert call["action"] == "deploy_preview"
    assert call["repo_path"] == str(clean_repo)
    assert call["branch"] == item.branch_name

    # Lock was acquired and then released.
    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "released"
    assert lock.release_reason == "preview_deployed"


def test_executor_failure_releases_lock_and_marks_error(
    client, db_session, monkeypatch, clean_repo
):
    """Failed executor response → metadata stays unset, lock released failed."""
    item = _make_review_ready_item(db_session)
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(
        monkeypatch,
        _executor_failure(
            error="image build failed", error_code="executor_compose_build_failed"
        ),
    )

    response = client.post(f"/api/work-items/{item.id}/deploy-preview", json={})

    assert response.status_code == 500
    assert "build failed" in response.json()["detail"].lower()
    db_session.refresh(item)
    assert item.preview_status == "error"
    assert item.preview_deployed is not True
    assert item.preview_health_status == "unhealthy"
    assert "executor_compose_build_failed" in (item.preview_error or "")
    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "failed"
    assert lock.release_reason == "executor_compose_build_failed"


def test_executor_unreachable_returns_502(
    client, db_session, monkeypatch, clean_repo
):
    """A network error from the executor surfaces as 502, not 500."""
    item = _make_review_ready_item(db_session)
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(
        monkeypatch,
        preview_deploy.ExecutorResponse(
            success=False,
            error="executor unreachable: connection refused",
            error_code="executor_unreachable",
        ),
    )

    response = client.post(f"/api/work-items/{item.id}/deploy-preview", json={})

    assert response.status_code == 502
    db_session.refresh(item)
    assert item.preview_deployed is not True
    assert item.preview_status == "error"
    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "failed"


# ---------------------------------------------------------------------------
# revert-preview endpoint
# ---------------------------------------------------------------------------


def test_revert_requires_deployed_preview(client, db_session, monkeypatch, clean_repo):
    item = _make_review_ready_item(db_session)
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(monkeypatch, _executor_success())

    response = client.post(
        f"/api/work-items/{item.id}/revert-preview",
        json={"reason": "rolling back smoke test"},
    )

    assert response.status_code == 409
    assert "preview" in response.json()["detail"].lower()


def test_revert_calls_executor_with_base_branch_and_records_metadata(
    client, db_session, monkeypatch, clean_repo
):
    item = _make_review_ready_item(
        db_session,
        preview_deployed=True,
        preview_status="deployed",
        preview_branch="feature/wi-preview",
        preview_pr_number=42,
    )
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    calls = _stub_executor(monkeypatch, _executor_success())

    response = client.post(
        f"/api/work-items/{item.id}/revert-preview",
        json={"reason": "preview broke smoke test", "reverted_by": "operator-bob"},
    )

    assert response.status_code == 200, response.text
    # The revert flow called the executor with the project's base branch.
    assert len(calls) == 1
    assert calls[0]["action"] == "revert_preview"
    assert calls[0]["branch"] == preview_deploy.REVERT_BASE_BRANCH

    body = response.json()
    assert body["preview_status"] == "reverted"
    assert body["preview_deployed"] is False
    assert body["preview_reverted_at"] is not None
    assert body["preview_reverted_by"] == "operator-bob"
    assert body["preview_revert_reason"] == "preview broke smoke test"
    assert body["preview_health_status"] == "reverted"

    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "released"
    assert lock.release_reason == "preview_reverted"


def test_revert_blocked_when_repo_busy(client, db_session, monkeypatch, clean_repo):
    from app import repo_safety

    repo_safety.acquire_repo_lock(
        session=db_session,
        repo_path=str(clean_repo),
        repo_name="legion-dashboard",
        branch_name="main",
        commit_sha="a" * 40,
        task_id="prior-build",
        lock_owner="builder",
    )

    item = _make_review_ready_item(
        db_session, preview_deployed=True, preview_status="deployed"
    )
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    calls = _stub_executor(monkeypatch, _executor_success())

    response = client.post(
        f"/api/work-items/{item.id}/revert-preview",
        json={"reason": "operator force revert"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["blocker_code"] == "blocked_repo_busy"
    # The executor must NOT have been called.
    assert calls == []
    db_session.refresh(item)
    assert item.preview_deployed is True


def test_revert_with_empty_reason_returns_422(client, db_session, monkeypatch, clean_repo):
    """Empty / whitespace reason must be rejected by the schema validator."""
    item = _make_review_ready_item(
        db_session, preview_deployed=True, preview_status="deployed"
    )
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(monkeypatch, _executor_success())

    response = client.post(
        f"/api/work-items/{item.id}/revert-preview", json={"reason": "   "}
    )

    assert response.status_code == 422


def test_revert_executor_failure_marks_revert_error(
    client, db_session, monkeypatch, clean_repo
):
    item = _make_review_ready_item(
        db_session, preview_deployed=True, preview_status="deployed"
    )
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    _stub_executor(
        monkeypatch,
        _executor_failure(
            error="healthcheck failed", error_code="executor_healthcheck_failed"
        ),
    )

    response = client.post(
        f"/api/work-items/{item.id}/revert-preview", json={"reason": "broken build"}
    )

    assert response.status_code == 500
    db_session.refresh(item)
    # Still flagged as deployed because the revert failed.
    assert item.preview_deployed is True
    assert item.preview_status == "revert_error"
    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "failed"
    assert lock.release_reason == "executor_healthcheck_failed"


def test_revert_blocks_merged_work_item(client, db_session, monkeypatch, clean_repo):
    """A merged Work Item must not be revertable, even if still flagged deployed."""
    item = _make_review_ready_item(
        db_session,
        preview_deployed=True,
        preview_status="deployed",
        merge_commit_sha="d" * 40,
    )
    _stub_repo_resolution(monkeypatch, str(clean_repo))
    calls = _stub_executor(monkeypatch, _executor_success())

    response = client.post(
        f"/api/work-items/{item.id}/revert-preview",
        json={"reason": "attempted revert after merge"},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "merged" in detail.lower()
    # The lock was never acquired, the executor was never called.
    assert calls == []
    assert (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .count()
        == 0
    )
    db_session.refresh(item)
    assert item.preview_deployed is True
    assert item.preview_status == "deployed"
