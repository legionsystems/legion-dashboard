"""Tests for workflow slice 3 — repo safety gate and repo lock manager.

Covers the pure module (``app.repo_safety``) plus the builder-router
integration: dirty-repo / busy-repo / branch-mismatch must return 409 and
must NOT advance the work item into ``building``; lock is acquired before
the build starts and released when the build reaches a terminal status.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app import repo_safety
from app.models import BuilderTask, RepoLock, WorkItem
from app.routers import builder as builder_router


# ---------------------------------------------------------------------------
# Fixtures: a real, isolated git worktree we can dirty / clean per test.
# ---------------------------------------------------------------------------


def _git(repo_path: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in ``repo_path``. Raise on non-zero."""
    return subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def clean_repo(tmp_path):
    """Initialize a clean git repo with one commit on ``main``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    # Provide a .gitignore so .hermes-config.yaml is treated as ignored.
    (repo / ".gitignore").write_text(".hermes-config.yaml\n")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


# ---------------------------------------------------------------------------
# Unit tests — check_repo_clean
# ---------------------------------------------------------------------------


def test_clean_repo_passes_gate(clean_repo):
    result = repo_safety.check_repo_clean(str(clean_repo))
    assert result.is_clean is True
    assert result.blocker_code is None
    assert result.dirty_files == []
    assert result.staged_files == []
    assert result.untracked_files == []
    assert result.current_branch == "main"
    assert result.current_commit  # 40-char sha
    assert len(result.current_commit) == 40


def test_dirty_unstaged_file_blocks(clean_repo):
    (clean_repo / "README.md").write_text("dirty\n")

    result = repo_safety.check_repo_clean(str(clean_repo))

    assert result.is_clean is False
    assert result.blocker_code == "blocked_dirty_repo"
    assert "README.md" in result.dirty_files
    assert result.staged_files == []


def test_dirty_staged_file_blocks(clean_repo):
    (clean_repo / "README.md").write_text("staged change\n")
    _git(clean_repo, "add", "README.md")

    result = repo_safety.check_repo_clean(str(clean_repo))

    assert result.is_clean is False
    assert result.blocker_code == "blocked_dirty_repo"
    assert "README.md" in result.staged_files


def test_untracked_nonignored_file_blocks(clean_repo):
    (clean_repo / "new_thing.py").write_text("print('hi')\n")

    result = repo_safety.check_repo_clean(str(clean_repo))

    assert result.is_clean is False
    assert result.blocker_code == "blocked_dirty_repo"
    assert "new_thing.py" in result.untracked_files


def test_ignored_hermes_config_does_not_block(clean_repo):
    # Hermes config is in .gitignore — but even when it isn't, the gate
    # should still skip it because the gate filters .hermes-config.yaml
    # explicitly.
    (clean_repo / ".hermes-config.yaml").write_text("key: value\n")

    result = repo_safety.check_repo_clean(str(clean_repo))

    assert result.is_clean is True
    assert result.untracked_files == []


def test_hermes_config_ignored_even_when_tracked(clean_repo):
    # If .hermes-config.yaml is tracked AND dirty, the gate still ignores it
    # so a hermes-managed worktree can run builds.
    (clean_repo / ".gitignore").write_text("")
    (clean_repo / ".hermes-config.yaml").write_text("first: v\n")
    _git(clean_repo, "add", ".")
    _git(clean_repo, "commit", "-m", "add hermes config")
    # Now dirty the hermes file.
    (clean_repo / ".hermes-config.yaml").write_text("first: v2\n")

    result = repo_safety.check_repo_clean(str(clean_repo))

    assert result.is_clean is True


def test_repo_not_found_is_blocker():
    result = repo_safety.check_repo_clean("/nonexistent/path/to/repo")
    assert result.is_clean is False
    assert result.blocker_code == "repo_not_found"


# ---------------------------------------------------------------------------
# Unit tests — lock manager
# ---------------------------------------------------------------------------


def _acquire(db, repo_path, **overrides):
    payload = dict(
        repo_path=repo_path,
        repo_name="legion-dashboard",
        branch_name="main",
        commit_sha="a" * 40,
        work_item_id=None,
        task_id=None,
        lock_owner="builder",
    )
    payload.update(overrides)
    return repo_safety.acquire_repo_lock(db, **payload)


def test_acquire_lock_on_free_repo(db_session):
    result = _acquire(db_session, "/tmp/repo-a")

    assert result.acquired is True
    assert result.lock is not None
    assert result.lock.lock_status == "active"
    assert result.lock.released_at is None


def test_check_repo_busy_returns_active_lock(db_session):
    _acquire(db_session, "/tmp/repo-b")

    found = repo_safety.check_repo_busy(db_session, "/tmp/repo-b")
    assert found is not None
    assert found.repo_path == "/tmp/repo-b"


def test_repo_busy_lock_blocks_second_acquire(db_session):
    first = _acquire(db_session, "/tmp/repo-c")
    assert first.acquired is True

    second = _acquire(db_session, "/tmp/repo-c")

    assert second.acquired is False
    assert second.existing_lock is not None
    assert second.existing_lock.id == first.lock.id
    assert second.blocker_code == "blocked_repo_busy"


def test_release_lock_marks_released(db_session):
    res = _acquire(db_session, "/tmp/repo-d")
    lock_id = res.lock.id

    released = repo_safety.release_repo_lock(
        db_session, "/tmp/repo-d", release_reason="test"
    )

    assert released is not None
    assert released.id == lock_id
    assert released.lock_status == "released"
    assert released.released_at is not None
    assert released.release_reason == "test"

    # After release, the repo is no longer busy and can be re-acquired.
    assert repo_safety.check_repo_busy(db_session, "/tmp/repo-d") is None
    again = _acquire(db_session, "/tmp/repo-d")
    assert again.acquired is True


def test_release_no_active_lock_returns_none(db_session):
    out = repo_safety.release_repo_lock(db_session, "/tmp/unlocked-repo")
    assert out is None


def test_release_with_expected_task_id_matches(db_session):
    """When expected_task_id matches the active lock, release proceeds."""
    res = _acquire(db_session, "/tmp/repo-owner-match", task_id="hermes-owner-1")
    assert res.acquired is True

    released = repo_safety.release_repo_lock(
        db_session,
        "/tmp/repo-owner-match",
        release_reason="owner_terminal",
        expected_task_id="hermes-owner-1",
    )

    assert released is not None
    assert released.lock_status == "released"
    assert released.release_reason == "owner_terminal"


def test_release_with_expected_task_id_mismatch_leaves_lock_intact(db_session):
    """A stale terminal sync from an older task must not release another build's lock."""
    # Build A acquires, completes, releases.
    a = _acquire(db_session, "/tmp/repo-stale", task_id="hermes-task-A")
    assert a.acquired is True
    repo_safety.release_repo_lock(
        db_session,
        "/tmp/repo-stale",
        release_reason="a_done",
        expected_task_id="hermes-task-A",
    )

    # Build B acquires the same repo (recycles the row).
    b = _acquire(db_session, "/tmp/repo-stale", task_id="hermes-task-B")
    assert b.acquired is True
    b_lock_id = b.lock.id

    # A late terminal sync for A arrives, claiming to release the lock.
    out = repo_safety.release_repo_lock(
        db_session,
        "/tmp/repo-stale",
        release_reason="stale_a_done",
        expected_task_id="hermes-task-A",
    )

    # The mismatch is detected; B's lock is left untouched.
    assert out is None
    still_active = repo_safety.check_repo_busy(db_session, "/tmp/repo-stale")
    assert still_active is not None
    assert still_active.id == b_lock_id
    assert still_active.lock_status == "active"
    assert still_active.task_id == "hermes-task-B"


def test_release_with_no_expected_task_id_still_releases(db_session):
    """Manual-clear path (no expected_task_id) always releases the active lock."""
    res = _acquire(db_session, "/tmp/repo-manual", task_id="hermes-anything")
    assert res.acquired is True

    released = repo_safety.release_repo_lock(
        db_session, "/tmp/repo-manual", release_reason="operator_clear"
    )

    assert released is not None
    assert released.lock_status == "released"


def test_acquire_integrity_error_race_returns_blocked(db_session, monkeypatch):
    """Two acquires racing past existing_any return blocked_repo_busy, not 500.

    Simulates the race: both callers query ``existing_any`` and see no row,
    so both proceed to INSERT. The second commit must hit the unique
    ``repo_path`` constraint and be translated into a ``blocked_repo_busy``
    LockResult, not a 500.

    We simulate this by force-returning ``None`` from the ``existing_any``
    lookup on the loser's call — letting the real DB-level unique constraint
    raise ``IntegrityError`` at commit time.
    """
    # Winner acquires first via the normal path.
    winner = _acquire(
        db_session, "/tmp/repo-race", task_id="hermes-concurrent-winner"
    )
    assert winner.acquired is True

    # Patch the ORM lookup the loser does at the top of acquire_repo_lock so
    # the loser believes the repo is free, then proceeds to INSERT and hits
    # the DB-level unique constraint.
    real_query = db_session.query

    class _FakeFilterReturnsNone:
        def __init__(self, *_args, **_kwargs):
            pass

        def filter(self, *_args, **_kwargs):  # noqa: D401
            return self

        def one_or_none(self):
            return None

    call_count = {"n": 0}

    def fake_query(*args, **kwargs):
        # The first query in acquire_repo_lock is the existing_any lookup.
        # Return None so the loser falls through to the INSERT branch.
        # All subsequent queries (e.g. the post-rollback lookup) use the
        # real query so the existing winner is discoverable.
        if call_count["n"] == 0 and args and args[0] is RepoLock:
            call_count["n"] += 1
            return _FakeFilterReturnsNone()
        return real_query(*args, **kwargs)

    monkeypatch.setattr(db_session, "query", fake_query)

    result = _acquire(db_session, "/tmp/repo-race", task_id="hermes-loser")

    assert result.acquired is False
    assert result.blocker_code == "blocked_repo_busy"
    assert "concurrent" in (result.blocker_message or "").lower()
    assert result.existing_lock is not None
    assert result.existing_lock.task_id == "hermes-concurrent-winner"


# ---------------------------------------------------------------------------
# Endpoint tests — /api/repo-safety/check and /api/repo-locks
# ---------------------------------------------------------------------------


def test_check_endpoint_returns_clean(client, clean_repo):
    response = client.get(
        "/api/repo-safety/check", params={"repo_path": str(clean_repo)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_clean"] is True
    assert body["current_branch"] == "main"


def test_check_endpoint_returns_dirty(client, clean_repo):
    (clean_repo / "README.md").write_text("dirty\n")

    response = client.get(
        "/api/repo-safety/check", params={"repo_path": str(clean_repo)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_clean"] is False
    assert body["blocker_code"] == "blocked_dirty_repo"


def test_list_active_locks_endpoint(client, db_session):
    _acquire(db_session, "/tmp/list-active")

    response = client.get("/api/repo-locks")

    assert response.status_code == 200
    locks = response.json()
    assert any(l["repo_path"] == "/tmp/list-active" for l in locks)


def test_stale_lock_is_visible_and_manually_clearable(client, db_session):
    res = _acquire(db_session, "/tmp/stale-lock")
    lock_id = res.lock.id

    # Visible in active list.
    listed = client.get("/api/repo-locks").json()
    assert any(l["id"] == lock_id for l in listed)

    # Manually clearable.
    cleared = client.delete(f"/api/repo-locks/{lock_id}")
    assert cleared.status_code == 200
    assert cleared.json()["lock_status"] == "released"

    # No longer listed as active.
    listed_after = client.get("/api/repo-locks").json()
    assert not any(l["id"] == lock_id for l in listed_after)


def test_delete_unknown_lock_returns_404(client):
    response = client.delete("/api/repo-locks/99999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Integration tests — builder router gate
# ---------------------------------------------------------------------------


def _make_approved_work_item(db_session, **overrides) -> WorkItem:
    item = WorkItem(
        type="task",
        title="Gated build candidate",
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
    def fake_resolve(_work_item):
        return (repo_path, "legion-dashboard")
    monkeypatch.setattr(builder_router, "_resolve_target_repo", fake_resolve)


def _stub_hermes(monkeypatch, task_id: str = "hermes-task-1"):
    def fake_create(**kwargs):
        return {
            "task_id": task_id,
            "status": kwargs.get("status_override") or "ready",
            "assignee": kwargs.get("assignee") or "builder",
        }
    monkeypatch.setattr(builder_router, "_create_hermes_task", fake_create)


def test_dirty_repo_blocks_build_and_does_not_advance_work_item(
    client, db_session, monkeypatch, clean_repo
):
    item = _make_approved_work_item(db_session)
    (clean_repo / "README.md").write_text("dirty\n")

    _patch_target_repo(monkeypatch, str(clean_repo))
    _stub_hermes(monkeypatch)

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build",
        json={},
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["blocker_code"] == "blocked_dirty_repo"
    # No builder task was created.
    assert (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .count()
        == 0
    )
    # No lock was created.
    assert (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .count()
        == 0
    )
    # Effective state did not become ``building`` — refetch via API.
    refreshed = client.get(f"/api/work-items/{item.id}").json()
    assert refreshed["effective_state"] != "building"


def test_lock_acquired_before_build_start(
    client, db_session, monkeypatch, clean_repo
):
    item = _make_approved_work_item(db_session)
    _patch_target_repo(monkeypatch, str(clean_repo))
    _stub_hermes(monkeypatch, task_id="hermes-acquire-1")

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert response.status_code == 200, response.text

    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "active"
    assert lock.work_item_id == item.id
    assert lock.task_id == "hermes-acquire-1"
    assert lock.branch_name == "main"


def test_repo_busy_lock_blocks_second_build(
    client, db_session, monkeypatch, clean_repo
):
    first_item = _make_approved_work_item(db_session, title="First")
    second_item = _make_approved_work_item(db_session, title="Second")

    _patch_target_repo(monkeypatch, str(clean_repo))
    _stub_hermes(monkeypatch, task_id="hermes-busy-1")

    first = client.post(
        f"/api/builder/work-items/{first_item.id}/start-build", json={}
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/builder/work-items/{second_item.id}/start-build", json={}
    )

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["blocker_code"] == "blocked_repo_busy"
    assert detail["existing_lock_work_item_id"] == first_item.id


def test_lock_released_on_successful_completion(
    client, db_session, monkeypatch, clean_repo
):
    item = _make_approved_work_item(db_session)
    _patch_target_repo(monkeypatch, str(clean_repo))
    _stub_hermes(monkeypatch, task_id="hermes-done-1")

    started = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert started.status_code == 200
    builder_task_id = started.json()["id"]

    # Simulate Hermes reporting completion via the sync endpoint.
    def fake_urlopen(req, timeout=30):
        class _Resp:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *_): return False
            def read(self_inner):
                import json as _json
                return _json.dumps({
                    "task": {
                        "task": {
                            "id": "hermes-done-1",
                            "status": "done",
                            "assignee": "builder",
                            "result": "success",
                        }
                    }
                }).encode()
        return _Resp()
    monkeypatch.setattr(
        "urllib.request.urlopen", fake_urlopen
    )

    synced = client.post(f"/api/builder/tasks/{builder_task_id}/sync", json={})
    assert synced.status_code == 200

    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "released"
    assert lock.released_at is not None
    assert lock.release_reason == "build_completed"


def test_lock_released_on_build_failure(
    client, db_session, monkeypatch, clean_repo
):
    item = _make_approved_work_item(db_session)
    _patch_target_repo(monkeypatch, str(clean_repo))
    _stub_hermes(monkeypatch, task_id="hermes-fail-1")

    started = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert started.status_code == 200
    builder_task_id = started.json()["id"]

    def fake_urlopen(req, timeout=30):
        class _Resp:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *_): return False
            def read(self_inner):
                import json as _json
                return _json.dumps({
                    "task": {
                        "task": {
                            "id": "hermes-fail-1",
                            "status": "failed",
                            "assignee": "builder",
                            "result": "boom",
                        }
                    }
                }).encode()
        return _Resp()
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    synced = client.post(f"/api/builder/tasks/{builder_task_id}/sync", json={})
    assert synced.status_code == 200

    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "failed"
    assert lock.release_reason == "build_failed"


def test_branch_mismatch_blocks_build(
    client, db_session, monkeypatch, clean_repo
):
    item = _make_approved_work_item(
        db_session, branch_name="feature/expected-branch"
    )

    _patch_target_repo(monkeypatch, str(clean_repo))
    _stub_hermes(monkeypatch)

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["blocker_code"] == "blocked_branch_mismatch"
    assert detail["current_branch"] == "main"
    assert detail["expected_branch"] == "feature/expected-branch"

    # No builder task and no lock were created.
    assert (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .count()
        == 0
    )
    assert (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .count()
        == 0
    )
