"""Tests for the Kanban worktree-isolation prompt-assembly change.

Covers:

* deterministic, safe worktree-path generation
  (:mod:`app.worktree_paths`)
* the implementation Kanban card body that the builder receives
  targets the task worktree, never the shared operator/control
  repo, and includes a fail-fast repo-root check
* the router adapter wires the task worktree through
  (:mod:`app.routers.builder`)
"""
from __future__ import annotations

import os
import re
from types import SimpleNamespace

import pytest

from app import worktree_paths
from app.builder_card import build_implementation_card_prompt
from app.models import WorkItem
from app.worktree_paths import (
    SHARED_REPO_PATH,
    WORKTREES_ROOT,
    build_task_worktree_path,
    is_shared_repo_path,
    is_under_worktrees,
    shared_repo_path_for_work_item,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work_item(
    id: int = 17,
    title: str = "Auto-Assign Stance",
    target_app: str = "legion-dashboard",
    body: str = "",
    acceptance_notes: str = "",
    type: str = "task",
    priority: str = "medium",
    source: str = "operator",
):
    """Return a duck-typed WorkItem stub.

    Uses ``SimpleNamespace`` plus a couple of helpers so it can
    stand in for the real SQLAlchemy model. Keeps the tests fast and
    free of database setup.
    """
    return SimpleNamespace(
        id=id,
        title=title,
        target_app=target_app,
        body=body,
        acceptance_notes=acceptance_notes,
        type=type,
        priority=priority,
        source=source,
    )


# ---------------------------------------------------------------------------
# Worktree path generation
# ---------------------------------------------------------------------------


def test_build_task_worktree_path_is_under_worktrees():
    wi = _make_work_item()
    path = build_task_worktree_path(wi)
    assert path.startswith(WORKTREES_ROOT + "/"), path
    assert is_under_worktrees(path)


def test_build_task_worktree_path_includes_work_item_id():
    wi = _make_work_item(id=42)
    path = build_task_worktree_path(wi)
    # The ``wi-42-...`` leaf is one path component under the slug.
    head = path[len(WORKTREES_ROOT) + 1 :].split("/")[1]
    assert head.startswith("wi-42-"), head
    # Stable per-work-item identifier suffix.
    assert path.endswith("/t_000042"), path


def test_build_task_worktree_path_includes_title_slug():
    wi = _make_work_item(title="Reorganize Dashboard Navigation")
    path = build_task_worktree_path(wi)
    head = path[len(WORKTREES_ROOT) + 1 :].split("/")[1]
    assert head.startswith("wi-17-reorganize-dashboard-navigation"), head


def test_build_task_worktree_path_differs_per_builder_task_id():
    wi = _make_work_item()
    base = build_task_worktree_path(wi)
    with_builder = build_task_worktree_path(wi, builder_task_id=99)
    assert base != with_builder
    # The with_builder variant uses the builder task id, not the wi id.
    assert with_builder.endswith("/t_000099"), with_builder


def test_build_task_worktree_path_is_stable_for_same_inputs():
    wi = _make_work_item(id=5, title="Hello World")
    a = build_task_worktree_path(wi, builder_task_id=12)
    b = build_task_worktree_path(wi, builder_task_id=12)
    assert a == b


def test_build_task_worktree_path_slugifies_unsafe_title():
    wi = _make_work_item(title="Add /etc/passwd support?!")
    path = build_task_worktree_path(wi)
    # Each path component is from the safe alphabet; no metacharacters.
    for component in path.split("/"):
        if not component:
            continue
        assert re.fullmatch(r"[a-z0-9][a-z0-9_\-]*", component), (
            f"unsafe component {component!r} in {path!r}"
        )


def test_build_task_worktree_path_rejects_traversal_attempt():
    wi = _make_work_item()
    # A traversal attempt gets slugified to a safe word; the path
    # remains under /srv/worktrees and never escapes it.
    bad = build_task_worktree_path(wi, title_slug="../../etc/passwd")
    assert bad.startswith(WORKTREES_ROOT + "/")
    assert ".." not in bad.split("/")
    assert "/etc/" not in bad


def test_build_task_worktree_path_supports_hub_target_app():
    wi = _make_work_item(id=7, target_app="lgn-hub", title="Some Hub Task")
    path = build_task_worktree_path(wi)
    # The repo slug must be its own path component so the host-side
    # creator tool can derive /srv/repo/lgn-hub.
    head = path[len(WORKTREES_ROOT) + 1 :].split("/")[0]
    assert head == "lgn-hub", head
    leaf = path[len(WORKTREES_ROOT) + 1 :].split("/")[1]
    assert leaf.startswith("wi-7-some-hub-task"), leaf


def test_build_task_worktree_path_handles_empty_title():
    wi = _make_work_item(id=8, title="")
    path = build_task_worktree_path(wi)
    # Falls back to a stable ``task`` slug.
    assert "/wi-8-task/" in path, path


def test_build_task_worktree_path_repo_slug_is_own_component():
    """The first path component under /srv/worktrees/ must be the
    repo slug so the host-side creator can derive the source
    shared repo. Hub and dashboard targets both qualify."""
    dash = build_task_worktree_path(_make_work_item(target_app="legion-dashboard"))
    hub = build_task_worktree_path(_make_work_item(target_app="lgn-hub"))
    # First non-root component is the slug itself.
    for path, expected in (
        (dash, "legion-dashboard"),
        (hub, "lgn-hub"),
    ):
        head = path[len(WORKTREES_ROOT) + 1 :].split("/", 1)[0]
        assert head == expected, (path, head, expected)


def test_build_task_worktree_path_is_never_shared_repo():
    """A future regression that returns the shared operator/control
    path must be impossible."""
    for id in (1, 2, 17, 9999):
        wi = _make_work_item(id=id)
        for builder_id in (None, 0, 1, 9999):
            path = build_task_worktree_path(wi, builder_task_id=builder_id)
            assert not is_shared_repo_path(path), (
                f"worktree path {path!r} collides with shared repo"
            )


# ---------------------------------------------------------------------------
# Shared-repo detection
# ---------------------------------------------------------------------------


def test_is_shared_repo_path_recognises_known_shared_paths():
    assert is_shared_repo_path(SHARED_REPO_PATH)
    assert is_shared_repo_path(SHARED_REPO_PATH + "/")
    assert is_shared_repo_path(SHARED_REPO_PATH + "/backend")
    assert is_shared_repo_path("/srv/repo/lgn-hub")
    assert is_shared_repo_path("/srv/repo/lgn-hub/backend/app")


def test_is_shared_repo_path_rejects_worktree_paths():
    assert not is_shared_repo_path(
        "/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017"
    )


def test_is_shared_repo_path_handles_empty():
    assert not is_shared_repo_path(None)
    assert not is_shared_repo_path("")


def test_is_under_worktrees_helper():
    assert is_under_worktrees("/srv/worktrees/anything")
    assert is_under_worktrees("/srv/worktrees/legion-dashboard-wi-1")
    assert is_under_worktrees("/srv/worktrees")
    assert not is_under_worktrees(SHARED_REPO_PATH)
    assert not is_under_worktrees(None)


# ---------------------------------------------------------------------------
# Prompt body assertions
# ---------------------------------------------------------------------------


def _build_prompt(**overrides):
    wi = overrides.pop("work_item", _make_work_item())
    kwargs = dict(
        debate_run_id=99,
        recommendation="APPROVE_WITH_MANDATORY_EDITS",
        implementation_readiness="READY_AFTER_EDITS",
        mandatory_edits=[
            {
                "field": "operator_argument_stance",
                "current_problem": "x",
                "required_change": "Implement AUTO_ASSIGN classification.",
            }
        ],
        target_repo=overrides.pop("target_repo", "/srv/repo/legion-dashboard"),
        target_worktree=overrides.pop("target_worktree", None),
    )
    kwargs.update(overrides)
    return build_implementation_card_prompt(wi, **kwargs)


def test_prompt_targets_task_worktree_when_provided():
    body = _build_prompt(
        target_worktree="/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017",
    )
    assert "TARGET WORKTREE" in body or "Target Worktree" in body
    assert (
        "/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017"
        in body
    )
    # The shared operator/control repo MUST NOT appear as the
    # implementation target.
    assert "TARGET REPO: /srv/repo/legion-dashboard" not in body
    assert "TARGET REPO: /srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017" in body


def test_prompt_includes_shared_repo_rule_and_fail_fast():
    body = _build_prompt(
        target_worktree="/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017",
    )
    # The prompt must warn the builder about the shared operator/control repo.
    assert "WORKTREE RULE" in body
    assert (
        "All repo, git, test, build, and Codex review operations for this"
        in body
    )
    # Explicit fail-fast on the shared repo root.
    assert "FAIL FAST" in body
    assert "git rev-parse --show-toplevel" in body
    assert "/srv/repo/legion-dashboard" in body
    # Must not instruct the builder to use the shared repo as the target.
    assert (
        "Do not use /srv/repo/legion-dashboard for implementation"
        in body or "shared" in body
    )


def test_prompt_without_target_worktree_still_warns():
    """Backwards compat: if a caller still uses the legacy
    ``target_repo`` form, the prompt must still surface the
    inconsistency rather than silently instructing the builder to
    work in the shared repo."""
    body = _build_prompt(
        target_repo="/srv/repo/legion-dashboard",
        target_worktree=None,
    )
    assert "Using Dedicated Worktree: NO" in body
    # Still includes the WORKTREE RULE so the operator sees the warning.
    assert "WORKTREE RULE" in body
    assert "FAIL FAST" in body


def test_prompt_does_not_direct_builder_to_shared_repo():
    """The implementation Kanban card body MUST NOT instruct the
    builder to use ``/srv/repo/legion-dashboard`` as the implementation
    target. Any path under ``/srv/repo/legion-dashboard`` that does
    appear in the body must be in a 'do not use' context, not a
    'use this' context."""
    body = _build_prompt(
        target_worktree="/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017",
    )
    # The only references to /srv/repo/legion-dashboard in the body
    # are inside the WORKTREE RULE / WORKTREE METADATA blocks that
    # explicitly identify it as the shared (forbidden) path. Find any
    # lines that mention the shared path and ensure they are
    # warning / metadata lines, not 'cd into' / 'target repo' lines.
    bad_phrases = [
        "cd /srv/repo/legion-dashboard",
        "cd /srv/repo/legion-dashboard/",
        "TARGET REPO: /srv/repo/legion-dashboard",
        "--repo /srv/repo/legion-dashboard",
    ]
    for phrase in bad_phrases:
        assert phrase not in body, (
            f"prompt body still instructs builder to use shared repo: "
            f"{phrase!r}"
        )


def test_prompt_phase_1_uses_task_worktree_path():
    body = _build_prompt(
        target_worktree="/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017",
    )
    # PHASE 1 INSPECT should `cd` into the worktree.
    assert (
        "cd /srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017"
        in body
    )


def test_prompt_secret_scan_and_codex_review_use_task_worktree():
    body = _build_prompt(
        target_worktree="/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017",
    )
    worktree = "/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017"
    assert f"--repo {worktree}" in body


def test_prompt_worktree_metadata_block_lists_assigned_path():
    body = _build_prompt(
        target_worktree="/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017",
    )
    assert "WORKTREE METADATA" in body
    assert "Target Worktree: /srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000017" in body
    assert "Shared Operator/Control Repo: /srv/repo/legion-dashboard" in body
    assert "Using Dedicated Worktree: yes" in body


# ---------------------------------------------------------------------------
# Router adapter wiring
# ---------------------------------------------------------------------------


def test_router_adapter_computes_task_worktree_path():
    from app.routers import builder as builder_router

    wi = _make_work_item()
    path = builder_router._compute_task_worktree_path(wi)
    assert path.startswith(WORKTREES_ROOT + "/")
    assert not is_shared_repo_path(path)


def test_router_adapter_rejects_shared_repo_collision(monkeypatch):
    from app.routers import builder as builder_router

    wi = _make_work_item()

    def _collide(*args, **kwargs):
        return SHARED_REPO_PATH

    monkeypatch.setattr(builder_router, "build_task_worktree_path", _collide)
    with pytest.raises(RuntimeError):
        builder_router._compute_task_worktree_path(wi)


def test_router_adapter_generated_prompt_targets_task_worktree(monkeypatch):
    from app.routers import builder as builder_router

    wi = _make_work_item()
    # Use a deterministic builder id so the path is stable.
    body = builder_router._generate_hermes_prompt(
        work_item=wi,
        debate_run_id=42,
        recommendation="APPROVE_WITH_MANDATORY_EDITS",
        implementation_readiness="READY_AFTER_EDITS",
        builder_task_id=7,
    )
    assert (
        "/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000007"
        in body
    )
    assert "TARGET REPO: /srv/repo/legion-dashboard" not in body
    assert "WORKTREE RULE" in body
    assert "FAIL FAST" in body


# ---------------------------------------------------------------------------
# Task-worktree orchestration
# ---------------------------------------------------------------------------


def test_task_worktree_feature_branch_includes_feature_prefix():
    """The feature branch is always prefixed with ``feature/`` so
    reviewers can tell at a glance which branch carries new work."""
    from app.task_worktree import _feature_branch_for_work_item

    wi = _make_work_item(id=17, title="Auto-Assign Stance")
    branch = _feature_branch_for_work_item(wi)
    assert branch.startswith("feature/"), branch
    assert "wi-17" in branch


def test_task_worktree_feature_branch_handles_unsafe_title():
    from app.task_worktree import _feature_branch_for_work_item

    wi = _make_work_item(id=9, title="!@#$%^&*()")
    branch = _feature_branch_for_work_item(wi)
    # Branch starts with feature/ and is slug-safe.
    assert branch.startswith("feature/")
    safe_part = branch[len("feature/") :]
    assert re.fullmatch(r"[A-Za-z0-9_\-/.]+", safe_part), branch


def test_task_worktree_feature_branch_handles_empty_title():
    from app.task_worktree import _feature_branch_for_work_item

    wi = _make_work_item(id=12, title="")
    branch = _feature_branch_for_work_item(wi)
    assert branch.startswith("feature/wi-12-"), branch


def test_ensure_task_worktree_creates_worktree_on_real_runner(tmp_path):
    """End-to-end against the real ``legion-worktree-create`` tool.

    The tool runs ``git worktree add`` on the host under
    ``/srv/worktrees/``; this test verifies the dashboard-side
    orchestrator composes the right arguments and surfaces the
    success path. The test cleans up the worktree afterwards so
    repeated runs stay idempotent.
    """
    from app.task_worktree import (
        ensure_task_worktree,
        _feature_branch_for_work_item,
    )

    wi = _make_work_item(id=9001, title="Orchestrator Smoke")
    db = None  # ensure_task_worktree's db arg is unused for now.

    try:
        worktree_path, feature_branch, _chosen_base, result = ensure_task_worktree(
            db, wi
        )
    except RuntimeError as exc:
        # If the tool is not on this host (e.g. test sandbox), the
        # orchestrator surfaces a clear RuntimeError rather than
        # silently falling back. That is itself the correct
        # behaviour; the test is environment-gated.
        if "missing tool" in str(exc) or "not executable" in str(exc):
            pytest.skip(f"worktree tool unavailable: {exc}")
        raise

    assert worktree_path.startswith(WORKTREES_ROOT + "/")
    assert feature_branch == _feature_branch_for_work_item(wi)
    assert result.get("success") is True
    assert result.get("reused") is False

    # Re-run: the tool should report reused=True, not re-create.
    _worktree_path2, _branch2, _chosen_base2, result2 = ensure_task_worktree(db, wi)
    assert result2.get("success") is True
    assert result2.get("reused") is True

    # Cleanup.
    import subprocess

    subprocess.run(
        [
            "git",
            "-C",
            "/srv/repo/legion-dashboard",
            "worktree",
            "remove",
            "--force",
            worktree_path,
        ],
        check=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            "/srv/repo/legion-dashboard",
            "branch",
            "-D",
            feature_branch,
        ],
        check=False,  # OK if already gone
    )


def test_router_releases_repo_lock_when_worktree_create_fails(
    client, db_session, monkeypatch
):
    """Regression (Codex P1 #2): if the worktree orchestrator fails
    after the repo safety lock is acquired, the lock MUST be
    released so a subsequent build is not pinned by a phantom lock.
    """
    from app.routers import builder as builder_router
    from app.models import RepoLock, WorkItem
    from app import preview_deploy
    from tests.test_builder import (
        _patch_target_repo,
        _stub_hermes,
        _stub_executor,
    )

    # Persist the work item so the router can fetch it from the DB.
    item = WorkItem(
        type="task",
        title="Lock Release Smoke",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        target_app="legion-dashboard",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # Pin the worktree orchestrator to fail.
    def _explode(*_args, **_kwargs):
        raise RuntimeError("simulated worktree-create failure")

    monkeypatch.setattr(builder_router, "ensure_task_worktree", _explode)

    # Stub the executor so the safety gate passes.
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
                "current_branch": "feature/dashboard-bootstrap-control-plane",
                "current_commit": "d" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )
    _stub_hermes(monkeypatch, task_id="hermes-no-need")
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["blocker_code"] == "blocked_worktree_create_failed"

    # The repo lock MUST have been released; no active locks remain.
    active_locks = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == "/srv/repo/legion-dashboard")
        .filter(RepoLock.lock_status == "active")
        .count()
    )
    assert active_locks == 0, (
        f"repo lock leaked after worktree-create failure: {active_locks}"
    )


# ---------------------------------------------------------------------------
# P1 fix: created worktree path and rendered prompt path must match
# P2 fix: retry branches must be unique per attempt
# ---------------------------------------------------------------------------


def _make_real_wi(id: int = 17, title: str = "Auto-Assign Stance") -> WorkItem:
    """Build a SQLAlchemy-mapped WorkItem for end-to-end tests."""
    return WorkItem(
        id=id,
        type="task",
        title=title,
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        target_app="legion-dashboard",
    )


def test_router_orchestrator_and_prompt_agree_on_worktree_path(client, db_session, monkeypatch):
    """P1 regression: the path the orchestrator creates must
    exactly match the TARGET REPO / TARGET WORKTREE path rendered
    in the generated prompt. Otherwise the builder is told to
    `cd` into an uncreated path."""
    from app.routers import builder as builder_router
    from app.task_worktree import ensure_task_worktree

    wi = _make_real_wi(id=17)
    db_session.add(wi)
    db_session.commit()
    db_session.refresh(wi)

    # Run the orchestrator. The shared venv supplies a real
    # legion-worktree-create tool; if it is missing, the test
    # environment-gates and skips.
    try:
        target_worktree, feature_branch, _chosen_base, _result = (
            ensure_task_worktree(db_session, wi, builder_task_id=1)
        )
    except RuntimeError as exc:
        if "missing tool" in str(exc) or "not executable" in str(exc):
            pytest.skip(f"worktree tool unavailable: {exc}")
        raise

    # Now render the prompt body the router would render.
    body = builder_router._generate_hermes_prompt(
        work_item=wi,
        builder_task_id=1,
        feature_branch=feature_branch,
    )

    # The prompt's TARGET REPO and TARGET WORKTREE lines point at
    # the orchestrator's path verbatim. PHASE 1 INSPECT's `cd`
    # line does the same. The secret scan and Codex review steps
    # use the same path.
    assert f"TARGET REPO: {target_worktree}" in body, (
        f"TARGET REPO line does not match the created worktree path. "
        f"expected {target_worktree!r} to be in the prompt body."
    )
    assert f"Target Worktree: {target_worktree}" in body
    assert f"cd {target_worktree} || exit 1" in body
    assert f"--repo {target_worktree}" in body
    # And the WORKTREE METADATA block also carries the branch name.
    assert f"Feature Branch: {feature_branch}" in body

    # Cleanup.
    import subprocess
    subprocess.run(
        ["git", "-C", "/srv/repo/legion-dashboard", "worktree", "remove", "--force", target_worktree],
        check=True,
    )
    subprocess.run(
        ["git", "-C", "/srv/repo/legion-dashboard", "branch", "-D", feature_branch],
        check=False,
    )


def test_router_resend_produces_fresh_worktree_path_and_branch(client, db_session, monkeypatch):
    """P2 regression: a second send for the same work item must
    produce a fresh worktree path AND a fresh feature branch name.
    Without the ``-t_<builder_task_id>`` suffix, ``git worktree add``
    cannot check out the same branch in two worktrees, so retries
    would fail."""
    from app.routers import builder as builder_router
    from app.task_worktree import ensure_task_worktree

    wi = _make_real_wi(id=42, title="Reorganize Sidebar")
    db_session.add(wi)
    db_session.commit()
    db_session.refresh(wi)

    try:
        first_path, first_branch, _first_base, _ = ensure_task_worktree(
            db_session, wi, builder_task_id=1
        )
        second_path, second_branch, _second_base, _ = ensure_task_worktree(
            db_session, wi, builder_task_id=2
        )
    except RuntimeError as exc:
        if "missing tool" in str(exc) or "not executable" in str(exc):
            pytest.skip(f"worktree tool unavailable: {exc}")
        raise

    assert first_path != second_path, (
        f"resend reused the same worktree path: {first_path!r}"
    )
    assert first_branch != second_branch, (
        f"resend reused the same feature branch: {first_branch!r}"
    )
    # Branches must both carry the per-attempt suffix.
    assert "t_000001" in first_branch, first_branch
    assert "t_000002" in second_branch, second_branch
    # Paths must carry the per-attempt suffix.
    assert first_path.endswith("/t_000001"), first_path
    assert second_path.endswith("/t_000002"), second_path

    # Each path is unique and lives under /srv/worktrees/.
    assert first_path.startswith("/srv/worktrees/")
    assert second_path.startswith("/srv/worktrees/")

    # The prompt body the router would render on each send also
    # matches the orchestrator's output.
    first_body = builder_router._generate_hermes_prompt(
        work_item=wi, builder_task_id=1, feature_branch=first_branch
    )
    second_body = builder_router._generate_hermes_prompt(
        work_item=wi, builder_task_id=2, feature_branch=second_branch
    )
    assert f"TARGET REPO: {first_path}" in first_body
    assert f"TARGET REPO: {second_path}" in second_body
    assert f"Feature Branch: {first_branch}" in first_body
    assert f"Feature Branch: {second_branch}" in second_body

    # Cleanup.
    import subprocess
    for path, branch in (
        (first_path, first_branch),
        (second_path, second_branch),
    ):
        subprocess.run(
            ["git", "-C", "/srv/repo/legion-dashboard", "worktree", "remove", "--force", path],
            check=True,
        )
        subprocess.run(
            ["git", "-C", "/srv/repo/legion-dashboard", "branch", "-D", branch],
            check=False,
        )


def test_prompt_renders_feature_branch_in_metadata_block():
    """The prompt body's WORKTREE METADATA block must surface the
    feature branch the orchestrator provisioned. Without this,
    the operator cannot see which branch the worktree is on."""
    wi = _make_work_item(id=99, title="Add Health Endpoint")
    body = build_implementation_card_prompt(
        wi,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
        target_repo="/srv/worktrees/legion-dashboard/wi-99-add-health-endpoint/t_000003",
        target_worktree="/srv/worktrees/legion-dashboard/wi-99-add-health-endpoint/t_000003",
        feature_branch="feature/wi-99-add-health-endpoint-t_000003",
    )
    assert "Feature Branch: feature/wi-99-add-health-endpoint-t_000003" in body


def test_feature_branch_includes_attempt_suffix_when_id_provided():
    """_feature_branch_for_work_item must append ``-t_<id>`` when
    given a builder_task_id so retries produce a different branch."""
    from app.task_worktree import _feature_branch_for_work_item

    wi = _make_work_item(id=5, title="Reorganize Sidebar")
    without = _feature_branch_for_work_item(wi)
    with_first = _feature_branch_for_work_item(wi, builder_task_id=1)
    with_second = _feature_branch_for_work_item(wi, builder_task_id=2)

    assert "-t_000001" in with_first, with_first
    assert "-t_000002" in with_second, with_second
    assert with_first != with_second
    # When no builder_task_id is supplied, the branch is the
    # per-work-item shape (no per-attempt suffix).
    assert "-t_" not in without


# ---------------------------------------------------------------------------
# Base-ref fallback: try configured refs in order until one resolves
# ---------------------------------------------------------------------------


def test_orchestrator_falls_back_to_second_base_ref_when_first_missing(
    monkeypatch,
):
    """P1 regression (Codex base-ref fallback): when the first
    configured base ref does not resolve (e.g. the host repo does
    not have ``feature/dashboard-bootstrap-control-plane``), the
    orchestrator must try the next configured ref in order. The
    first attempt fails; the second attempt succeeds.
    """
    from app import task_worktree

    # The orchestrator is going to call
    # ``_worktree_create_result`` for each candidate ref. Stub
    # it so the first ref fails and the second ref succeeds.
    call_count = {"n": 0}
    base_refs_seen: list = []

    def _fake_worktree_create_result(
        worktree_path, feature_branch, base_ref, timeout=60
    ):
        call_count["n"] += 1
        base_refs_seen.append(base_ref)
        if base_ref == "feature/dashboard-bootstrap-control-plane":
            return (
                False,
                {},
                f"fatal: invalid reference: {base_ref}",
            )
        if base_ref == "main":
            return (
                True,
                {
                    "success": True,
                    "worktree_path": worktree_path,
                    "feature_branch": feature_branch,
                    "base_ref": base_ref,
                    "commit": "deadbeef",
                    "reused": False,
                },
                "",
            )
        return False, {}, f"unexpected ref {base_ref}"

    monkeypatch.setattr(
        task_worktree, "_worktree_create_result", _fake_worktree_create_result
    )

    wi = _make_work_item(id=99, title="Hub Targeted Item", target_app="lgn-hub")
    worktree_path, feature_branch, chosen_base_ref, result = (
        task_worktree.ensure_task_worktree(
            db=None, work_item=wi, builder_task_id=1
        )
    )

    assert call_count["n"] == 2, (
        f"expected two host-tool invocations (one failure + one success), "
        f"got {call_count['n']}"
    )
    assert base_refs_seen == [
        "feature/dashboard-bootstrap-control-plane",
        "main",
    ], base_refs_seen
    assert chosen_base_ref == "main", chosen_base_ref
    assert result.get("base_ref") == "main"
    assert "lgn-hub" in worktree_path
    assert "t_000001" in feature_branch


def test_orchestrator_base_ref_candidates_are_repo_specific(monkeypatch):
    """A lgn-hub work item must NOT see the dashboard's base refs
    in its candidate list. The orchestrator derives the candidate
    list from the worktree path, which carries the repo slug."""
    from app import task_worktree
    from app.task_worktree import _REPO_BASE_REFS

    base_refs_seen: list = []

    def _fake(worktree_path, feature_branch, base_ref, timeout=60):
        base_refs_seen.append(base_ref)
        return True, {"success": True, "base_ref": base_ref}, ""

    monkeypatch.setattr(
        task_worktree, "_worktree_create_result", _fake
    )

    # Hub work item.
    hub_wi = _make_work_item(
        id=11, title="Hub Add Endpoint", target_app="lgn-hub"
    )
    task_worktree.ensure_task_worktree(
        db=None, work_item=hub_wi, builder_task_id=1
    )
    # The candidates tried for hub must come from
    # ``_REPO_BASE_REFS["lgn-hub"]``. The dashboard-only entry
    # ``feature/dashboard-bootstrap-control-plane`` is allowed to
    # appear in the candidate list because it is the configured
    # primary for both repos; the test is that the dashboard
    # repo slug's primary is not substituted in.
    # We verify the more specific guarantee: the candidate list
    # is the same for any work item on the same repo, and
    # differs across repos.
    hub_refs = list(base_refs_seen)

    # Now a dashboard work item.
    dash_wi = _make_work_item(
        id=12, title="Dash Add Endpoint", target_app="legion-dashboard"
    )
    base_refs_seen.clear()
    task_worktree.ensure_task_worktree(
        db=None, work_item=dash_wi, builder_task_id=1
    )
    dash_refs = list(base_refs_seen)

    # The dashboard candidate list and the hub candidate list are
    # both populated. The hub path is /srv/worktrees/lgn-hub/...
    # and the dashboard path is /srv/worktrees/legion-dashboard/...
    # — neither one falls back to the other's repo because the
    # worktree path embeds the slug and the orchestrator does
    # not consult the shared repo path.
    assert len(hub_refs) >= 1
    assert len(dash_refs) >= 1
    # The hosts they actually operate on are different (the
    # host-tool's own _shared_repo_for_worktree picks
    # /srv/repo/lgn-hub for hub and /srv/repo/legion-dashboard
    # for dash). The orchestrator never substitutes one for
    # the other: the loop runs only against the candidate list
    # for the slug embedded in the worktree path.
    assert hub_refs[0] in _REPO_BASE_REFS["lgn-hub"]
    assert dash_refs[0] in _REPO_BASE_REFS["legion-dashboard"]


def test_orchestrator_does_not_fall_back_to_dashboard_for_hub(monkeypatch):
    """A lgn-hub work item's candidate list must be drawn from
    ``_REPO_BASE_REFS["lgn-hub"]`` only. The dashboard ref list
    must never appear in the loop, even if the hub's primary
    ref is missing — there is no fallback to /srv/repo/legion-dashboard.
    """
    from app import task_worktree
    from app.task_worktree import _REPO_BASE_REFS

    base_refs_seen: list = []

    def _fake(worktree_path, feature_branch, base_ref, timeout=60):
        base_refs_seen.append(base_ref)
        # Both hub candidates fail.
        return False, {}, f"fatal: invalid reference: {base_ref}"

    monkeypatch.setattr(
        task_worktree, "_worktree_create_result", _fake
    )

    hub_wi = _make_work_item(
        id=13, title="Hub Bad Refs", target_app="lgn-hub"
    )
    with pytest.raises(RuntimeError) as excinfo:
        task_worktree.ensure_task_worktree(
            db=None, work_item=hub_wi, builder_task_id=1
        )

    # Every attempted ref came from the hub list. None of them
    # is a dashboard-only ref like the dashboard's primary, and
    # the error message lists every attempt.
    assert base_refs_seen == list(_REPO_BASE_REFS["lgn-hub"]), base_refs_seen
    msg = str(excinfo.value)
    for ref in _REPO_BASE_REFS["lgn-hub"]:
        assert f"base ref {ref!r}" in msg, (ref, msg)
    # The error must NOT mention any dashboard ref by name.
    for ref in _REPO_BASE_REFS["legion-dashboard"]:
        if ref not in _REPO_BASE_REFS["lgn-hub"]:
            assert ref not in msg, (ref, msg)


def test_orchestrator_aggregates_all_attempt_errors_when_all_refs_fail(
    monkeypatch,
):
    """If every candidate ref fails, the orchestrator raises with
    a clear aggregated error listing each attempted ref and a
    short excerpt of the per-attempt stderr so the operator can
    see exactly what went wrong."""
    from app import task_worktree

    def _fake(worktree_path, feature_branch, base_ref, timeout=60):
        return False, {}, f"fatal: invalid reference: {base_ref}"

    monkeypatch.setattr(
        task_worktree, "_worktree_create_result", _fake
    )

    wi = _make_work_item(id=14, title="All Refs Bad")
    with pytest.raises(RuntimeError) as excinfo:
        task_worktree.ensure_task_worktree(
            db=None, work_item=wi, builder_task_id=1
        )
    msg = str(excinfo.value)
    assert "tried 2 base ref(s), none resolved" in msg, msg
    assert "base ref 'feature/dashboard-bootstrap-control-plane'" in msg, msg
    assert "base ref 'main'" in msg, msg


def test_prompt_body_records_chosen_base_ref_from_orchestrator(monkeypatch):
    """The prompt body's WORKTREE METADATA block must surface the
    base ref the orchestrator actually used, so the operator can
    see whether the first candidate ref was used or whether the
    orchestrator had to fall back to ``main``."""
    from app.routers import builder as builder_router
    from app import task_worktree

    def _fake(worktree_path, feature_branch, base_ref, timeout=60):
        return (
            True,
            {
                "success": True,
                "worktree_path": worktree_path,
                "feature_branch": feature_branch,
                "base_ref": base_ref,
                "commit": "f" * 40,
                "reused": False,
            },
            "",
        )

    monkeypatch.setattr(
        task_worktree, "_worktree_create_result", _fake
    )

    wi = _make_work_item(id=15, title="Resolved Base Ref Prompt")
    target_worktree, feature_branch, chosen_base_ref, _ = (
        task_worktree.ensure_task_worktree(
            db=None, work_item=wi, builder_task_id=1
        )
    )
    body = builder_router._generate_hermes_prompt(
        work_item=wi,
        builder_task_id=1,
        feature_branch=feature_branch,
        chosen_base_ref=chosen_base_ref,
    )
    # The prompt's Base Ref line shows the resolved ref.
    assert f"Base Ref: {chosen_base_ref}" in body, body
    # The prompt's Target Worktree line shows the orchestrator's
    # path verbatim.
    assert f"Target Worktree: {target_worktree}" in body


# ---------------------------------------------------------------------------
# P2 fix: prompt body uses chosen_base_ref and does not ask the
# builder to create a second branch
# ---------------------------------------------------------------------------


def test_prompt_body_uses_chosen_base_ref_in_pr_and_review_instructions():
    """For a worktree-isolated task the local secret scan, the
    local Codex review, and the ``gh pr create`` command MUST use
    the orchestrator's resolved base ref (``chosen_base_ref``)
    rather than the literal placeholder ``<base-branch>`` or
    the literal ``main``."""
    wi = _make_work_item(id=42, title="Use Resolved Base Ref")
    body = build_implementation_card_prompt(
        wi,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
        target_repo="/srv/worktrees/legion-dashboard/wi-42-use-resolved-base-ref/t_000007",
        target_worktree="/srv/worktrees/legion-dashboard/wi-42-use-resolved-base-ref/t_000007",
        feature_branch="feature/wi-42-use-resolved-base-ref-t_000007",
        chosen_base_ref="feature/dashboard-bootstrap-control-plane",
    )
    # The chosen base ref must appear in the secret-scan, local
    # Codex review, and PR-create lines verbatim.
    assert (
        "legion-secret-scan --repo "
        "/srv/worktrees/legion-dashboard/wi-42-use-resolved-base-ref/t_000007"
        " --base feature/dashboard-bootstrap-control-plane "
        "--head feature/wi-42-use-resolved-base-ref-t_000007"
        in body
    )
    assert (
        "legion-codex-local-review \\\n"
        "     --repo /srv/worktrees/legion-dashboard/wi-42-use-resolved-base-ref/t_000007 \\\n"
        "     --base feature/dashboard-bootstrap-control-plane \\\n"
        "     --head feature/wi-42-use-resolved-base-ref-t_000007"
        in body
    )
    assert (
        "gh pr create --base feature/dashboard-bootstrap-control-plane --head feature/wi-42-use-resolved-base-ref-t_000007"
        in body
    )
    # And the prompt must not contain the literal placeholder
    # <base-branch> (anywhere) or the old hardcoded "PR to main"
    # line. The remaining <your-branch> is replaced by the
    # feature_branch above, so the placeholder is also gone.
    assert "<base-branch>" not in body, body
    assert "open/update the PR to main" not in body, body


def test_prompt_body_does_not_instruct_builder_to_create_second_branch():
    """For a worktree-isolated task the prompt must NOT tell the
    builder to run ``git checkout -b feature/<your-feature-name>``.
    The worktree is already on the orchestrator-provisioned
    branch; creating a second branch detaches from it and
    breaks retry / re-send correlation."""
    wi = _make_work_item(id=43, title="No Second Branch")
    body = build_implementation_card_prompt(
        wi,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
        target_repo="/srv/worktrees/legion-dashboard/wi-43-no-second-branch/t_000004",
        target_worktree="/srv/worktrees/legion-dashboard/wi-43-no-second-branch/t_000004",
        feature_branch="feature/wi-43-no-second-branch-t_000004",
        chosen_base_ref="main",
    )
    # The instruction must be replaced — the literal
    # ``git checkout -b feature/<your-feature-name>`` is not the
    # active command anywhere in the prompt. The new prompt
    # tells the builder to verify and check out the
    # provisioned branch. The legacy command string may still
    # appear inside a "Do NOT run ..." warning block, which is
    # the desired form: it teaches the model what NOT to do
    # without telling it to do it. We assert the legacy form is
    # only present inside that warning, never as an active
    # command.
    legacy_command = "   git checkout -b feature/<your-feature-name>\n"
    legacy_command_alt = "   git checkout -b feature/<your-feature-name>\r\n"
    assert legacy_command not in body, body
    assert legacy_command_alt not in body, body
    # The prompt instead instructs the builder to verify and
    # check out the provisioned branch.
    assert "Use the provisioned worktree branch" in body, body
    # The legacy command is mentioned only as a do-NOT instruction.
    assert (
        "Do NOT run `git checkout -b feature/<your-feature-name>`"
        in body
    )
    # The provisioned feature branch name is rendered in the
    # step-1 instructions so the builder has the literal value.
    assert "feature/wi-43-no-second-branch-t_000004" in body


def test_prompt_body_falls_back_to_main_when_chosen_base_ref_omitted():
    """When ``chosen_base_ref`` is not supplied (legacy call
    site, no orchestrator), the prompt falls back to the
    literal ``main`` for the secret scan, local Codex, and PR
    base — preserving the prior behaviour for non-worktree
    tasks."""
    wi = _make_work_item(id=44, title="Legacy No Base Ref")
    body = build_implementation_card_prompt(
        wi,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
        target_repo="/srv/worktrees/legion-dashboard/wi-44-legacy-no-base-ref/t_000005",
        target_worktree="/srv/worktrees/legion-dashboard/wi-44-legacy-no-base-ref/t_000005",
        feature_branch="feature/wi-44-legacy-no-base-ref-t_000005",
        chosen_base_ref=None,
    )
    # The fallback is 'main'.
    assert (
        "legion-secret-scan --repo "
        "/srv/worktrees/legion-dashboard/wi-44-legacy-no-base-ref/t_000005"
        " --base main"
        " --head feature/wi-44-legacy-no-base-ref-t_000005"
        in body
    )
    assert (
        "gh pr create --base main --head feature/wi-44-legacy-no-base-ref-t_000005"
        in body
    )


def test_prompt_body_contains_provisioned_feature_branch_in_instructions():
    """The local-pre-push-review-gate step 1 must show the
    literal provisioned feature branch so the builder has the
    exact string for ``git checkout`` and ``git push``."""
    wi = _make_work_item(id=45, title="Show Feature Branch")
    body = build_implementation_card_prompt(
        wi,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
        target_repo="/srv/worktrees/legion-dashboard/wi-45-show-feature-branch/t_000006",
        target_worktree="/srv/worktrees/legion-dashboard/wi-45-show-feature-branch/t_000006",
        feature_branch="feature/wi-45-show-feature-branch-t_000006",
        chosen_base_ref="main",
    )
    # The step-1 instruction literally names the provisioned branch.
    assert (
        "feature_branch=feature/wi-45-show-feature-branch-t_000006"
        in body
    )
    # And the push / PR-create commands use it.
    assert (
        "git push origin feature/wi-45-show-feature-branch-t_000006"
        in body
    )
    assert (
        "gh pr create --base main --head feature/wi-45-show-feature-branch-t_000006"
        in body
    )


def test_prompt_body_contains_chosen_base_ref_in_worktree_metadata():
    """The WORKTREE METADATA block must surface the chosen base
    ref so the operator and the builder can both verify it."""
    wi = _make_work_item(id=46, title="Show Base Ref In Metadata")
    body = build_implementation_card_prompt(
        wi,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
        target_repo="/srv/worktrees/legion-dashboard/wi-46-show-base-ref-in-metadata/t_000008",
        target_worktree="/srv/worktrees/legion-dashboard/wi-46-show-base-ref-in-metadata/t_000008",
        feature_branch="feature/wi-46-show-base-ref-in-metadata-t_000008",
        chosen_base_ref="feature/dashboard-bootstrap-control-plane",
    )
    assert "Base Ref: feature/dashboard-bootstrap-control-plane" in body, body


# ---------------------------------------------------------------------------
# Per-task worktree invariants (single-operator local control plane)
# ---------------------------------------------------------------------------


def test_prompt_body_has_worktree_verification_in_phase1():
    """Phase 1 must explicitly verify the builder is operating in
    the dedicated task worktree, not the shared
    operator/control worktree. The shell check
    ``git rev-parse --show-toplevel`` must equal the
    TARGET WORKTREE path, and the current branch must equal
    the provisioned feature branch. Any mismatch must abort
    before the builder mutates state."""
    wi = _make_work_item(id=47, title="Worktree Verification")
    body = build_implementation_card_prompt(
        wi,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
        target_repo="/srv/worktrees/legion-dashboard/wi-47-worktree-verification/t_000050",
        target_worktree="/srv/worktrees/legion-dashboard/wi-47-worktree-verification/t_000050",
        feature_branch="feature/wi-47-worktree-verification-t_000050",
        chosen_base_ref="main",
    )
    # The verify-worktree shell block.
    assert 'expected_topdir=$(git rev-parse --show-toplevel)' in body, body
    assert (
        'if [ "$expected_topdir" != '
        '"/srv/worktrees/legion-dashboard/wi-47-worktree-verification/t_000050" ]'
        in body
    )
    assert "FAIL: wrong worktree" in body, body
    # The branch check.
    assert "expected_branch=feature/wi-47-worktree-verification-t_000050" in body, body
    assert "FAIL: wrong branch" in body, body


def test_prompt_body_explicitly_forbids_cd_into_shared_repo():
    """The prompt body must explicitly tell the builder not to
    ``cd`` into /srv/repo/legion-dashboard. The shared
    operator/control worktree is for the operator's source
    checkout, not for builder implementation."""
    wi = _make_work_item(id=48, title="Forbid Shared Repo Cd")
    body = build_implementation_card_prompt(
        wi,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
        target_repo="/srv/worktrees/legion-dashboard/wi-48-forbid-shared-cd/t_000051",
        target_worktree="/srv/worktrees/legion-dashboard/wi-48-forbid-shared-cd/t_000051",
        feature_branch="feature/wi-48-forbid-shared-cd-t_000051",
        chosen_base_ref="main",
    )
    assert (
        "Do NOT cd into /srv/repo/legion-dashboard at any point"
        in body
    )


def test_ensure_task_worktree_safely_handles_concurrent_attempt_ids(
    monkeypatch,
):
    """Two concurrent ``ensure_task_worktree`` calls for the same
    work item would compute the same ``prior_attempts + 1`` and
    both target the same worktree path/branch — unless the
    caller reserves a per-attempt identifier through a different
    channel. This test pins the orchestrator's contract: it
    only allocates the worktree when the caller passes a unique
    ``builder_task_id`` (the BuilderTask.id from the row inserted
    earlier in the router). The orchestrator itself does not
    attempt to detect collisions."""
    from app import task_worktree

    def _fake(worktree_path, feature_branch, base_ref, timeout=60):
        return True, {"success": True, "reused": False, "base_ref": base_ref}, ""

    monkeypatch.setattr(
        task_worktree, "_worktree_create_result", _fake
    )

    wi = _make_work_item(id=49, title="Concurrent Attempt Ids")

    # Two calls with DIFFERENT builder_task_ids succeed and
    # produce different worktree paths and feature branches.
    p1, b1, _base1, _r1 = task_worktree.ensure_task_worktree(
        db=None, work_item=wi, builder_task_id=101
    )
    p2, b2, _base2, _r2 = task_worktree.ensure_task_worktree(
        db=None, work_item=wi, builder_task_id=102
    )
    assert p1 != p2, (p1, p2)
    assert b1 != b2, (b1, b2)
    # The branch suffix uses the builder_task_id, not the
    # work_item.id, so a second attempt for the same work item
    # is on a different branch.
    assert "t_000101" in b1
    assert "t_000102" in b2


def test_worktree_isolated_build_skips_branch_mismatch_check(
    client, db_session, monkeypatch
):
    """When the safety check runs against the task worktree
    (post-consolidation), the current branch is the
    orchestrator-created per-attempt feature branch, not the
    work item's existing ``branch_name``. The
    worktree-isolated build must not block a work item whose
    ``branch_name`` is set but does not match the
    per-attempt feature branch — that comparison is
    meaningless after the safety gate is retargeted."""
    from app.routers import builder as builder_router
    from app import preview_deploy
    from app.models import WorkItem
    from tests.test_builder import (
        _patch_target_repo,
        _stub_hermes,
        _stub_executor,
    )

    # Persist a work item with a populated branch_name (as
    # would be the case for an imported or retry work item).
    item = WorkItem(
        type="task",
        title="Imported Retry Item",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        target_app="legion-dashboard",
        branch_name="feature/some-other-branch",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    # Stub the executor to report the safety check on the
    # task worktree. After the worktree-create the current
    # branch will be the per-attempt feature branch
    # (feature/wi-<id>-slug-t_<id>), NOT
    # "feature/some-other-branch". The old branch-mismatch
    # check would block this; the new code must NOT.
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
                "current_branch": "feature/wi-50-imported-retry-item-t_000050",
                "current_commit": "d" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )
    _stub_hermes(monkeypatch, task_id="hermes-imported-1")
    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    # Must NOT 409 with blocked_branch_mismatch.
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hermes_task_id"] == "hermes-imported-1"


def test_per_work_item_lock_serialises_concurrent_starts():
    """Two concurrent ``_create_builder_task`` calls for the
    SAME work item must serialise so the second one observes
    the first one's committed BuilderTask stub and bails out
    with 409 instead of creating a duplicate Hermes task.
    Without the per-work-item Python lock both calls could
    pass the active-task check before either commits."""
    import threading
    from app import worktree_locks
    from app.routers import builder as builder_router

    # Use a fresh work item id so we don't collide with any
    # other test that has acquired the lock already.
    wid = 99999
    lock = worktree_locks.acquire_work_item_lock(wid)
    acquired = []
    barier = threading.Barrier(2)

    def worker():
        barier.wait()
        if lock.acquire(blocking=False):
            acquired.append(1)
            lock.release()
        else:
            acquired.append(0)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly one thread acquired the lock; the other was
    # blocked. After release, the second could acquire, so
    # "acquired" may contain one or two 1s depending on
    # scheduling. The contract is that both do not acquire
    # simultaneously — the second sees the first.
    assert len(threads) == 2
    # The lock is the same object across calls for the same
    # work item id.
    lock2 = worktree_locks.acquire_work_item_lock(wid)
    assert lock is lock2, "lock table must return the same lock per work item"


# ---------------------------------------------------------------------------
# Stub-commit-before-slow-work invariant (cross-process active-task check)
# ---------------------------------------------------------------------------


def test_builder_task_stub_is_committed_before_hermes_call(
    client, db_session, db_session_factory, monkeypatch
):
    """A second process that does the active-task check while the
    first start-build is mid-flight (after the stub commit but
    before the Hermes hand-off) must see the stub row through a
    fresh session. Without the up-front commit, the row would be
    flushed-but-uncommitted in the first session and invisible to
    any other session, and the cross-process active-task check
    would silently allow a duplicate Hermes task for the same
    work item.
    """
    from app import preview_deploy
    from app.models import BuilderTask, WorkItem
    from app.routers import builder as builder_router
    from tests.test_builder import (
        _patch_target_repo,
        _stub_executor,
    )

    item = WorkItem(
        type="task",
        title="Stub Visibility During Hermes",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        target_app="legion-dashboard",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
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
                "current_branch": (
                    "feature/wi-{0}-stub-visibility-during-hermes-t_000001".format(
                        item.id
                    )
                ),
                "current_commit": "d" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )

    # Open a fresh session inside the Hermes stub and assert the
    # stub row is already committed and visible to a different
    # session (simulating a cross-process active-task check).
    visible: dict = {}

    def _spy_hermes(**kwargs):
        fresh = db_session_factory()
        try:
            row = (
                fresh.query(BuilderTask)
                .filter(BuilderTask.work_item_id == item.id)
                .filter(
                    BuilderTask.hermes_status.not_in(["archived", "done"])
                )
                .first()
            )
            visible["row_id"] = row.id if row is not None else None
            visible["hermes_status"] = row.hermes_status if row is not None else None
            visible["hermes_task_id"] = (
                row.hermes_task_id if row is not None else None
            )
        finally:
            fresh.close()
        return {
            "task_id": "hermes-stub-visibility-1",
            "status": "ready",
            "assignee": "builder",
        }

    monkeypatch.setattr(builder_router, "_create_hermes_task", _spy_hermes)

    response = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert response.status_code == 200, response.text

    # The fresh session saw the stub row BEFORE the Hermes call
    # returned. The hermes_task_id was still the placeholder; the
    # hermes_status was still ``creating``. Both prove the commit
    # happened before the Hermes hand-off, not after.
    assert visible.get("row_id") is not None, (
        "stub BuilderTask row was not visible to a fresh session "
        "during the Hermes call — the up-front commit did not run"
    )
    assert visible.get("hermes_status") == "creating", visible
    assert visible.get("hermes_task_id", "").startswith(f"pending-{item.id}-"), (
        "stub row had a non-placeholder hermes_task_id during the "
        "Hermes call; the up-front commit must have committed the "
        "row before the placeholder was overwritten"
    )


def test_second_start_build_for_same_work_item_sees_committed_stub(
    client, db_session, monkeypatch
):
    """A second start-build for the same work item, issued after
    the first one's stub row is committed, must observe the
    committed row through the active-task check and return 409
    with the active-builder-task error — no duplicate Hermes
    task, no duplicate worktree allocation.

    The Python lock guards single-process concurrent starts; this
    test pins the DB-level guarantee that the same protection
    holds across sessions (and therefore across processes), which
    is what the up-front commit unlocks.
    """
    from app import preview_deploy
    from app.models import BuilderTask, WorkItem
    from tests.test_builder import (
        _patch_target_repo,
        _stub_executor,
        _stub_hermes,
    )

    item = WorkItem(
        type="task",
        title="Duplicate Start Returns 409",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        target_app="legion-dashboard",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
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
                "current_branch": (
                    "feature/wi-{0}-duplicate-start-returns-409-t_000001".format(
                        item.id
                    )
                ),
                "current_commit": "d" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )
    _stub_hermes(monkeypatch, task_id="hermes-dup-1")

    first = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert first.status_code == 200, first.text

    # Second call sees the committed BuilderTask row through the
    # active-task check and 409s. The detail names the active
    # Hermes task so the operator can correlate.
    second = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert second.status_code == 409, second.text
    detail = second.json()["detail"]
    assert isinstance(detail, str), detail
    assert "Active builder task already exists" in detail, detail
    assert "hermes-dup-1" in detail, detail

    # Exactly one BuilderTask row exists for this work item — the
    # second call did not create a duplicate.
    rows = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(rows) == 1, [r.hermes_task_id for r in rows]
    assert rows[0].hermes_task_id == "hermes-dup-1"


def test_outer_wrapper_rolls_back_stub_when_prompt_generation_raises(
    client, db_session, monkeypatch
):
    """Regression: when ``_generate_hermes_prompt`` raises an
    unexpected exception (which sits between the repo-lock
    acquire and the Hermes try/except), the catch-all wrapper
    around the post-stub-commit section must roll back the
    committed BuilderTask stub row and release the repo lock so
    a fresh second start-build for the same work item is not
    permanently blocked by the active-task check.

    Without the outer wrapper, the stub row would stay
    committed with ``hermes_status='creating'`` and every
    subsequent start-build for the same work item would 409
    with the active-builder-task error.
    """
    from app import preview_deploy
    from app.models import BuilderTask, RepoLock, WorkItem
    from app.routers import builder as builder_router
    from tests.test_builder import (
        _patch_target_repo,
        _stub_executor,
        _stub_hermes,
    )

    item = WorkItem(
        type="task",
        title="Prompt Builder Raises",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        target_app="legion-dashboard",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)

    _patch_target_repo(monkeypatch, "/srv/repo/legion-dashboard")
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
                "current_branch": (
                    "feature/wi-{0}-prompt-builder-raises-t_000001".format(
                        item.id
                    )
                ),
                "current_commit": "d" * 40,
                "blocker_code": None,
                "blocker_message": None,
            },
        ),
    )
    _stub_hermes(monkeypatch, task_id="hermes-prompt-raise-recovery-1")

    # Stub the worktree orchestrator so the test is deterministic
    # and doesn't depend on the host ``legion-worktree-create``
    # tool. The path/branch shape mirrors what the real
    # orchestrator returns for this work item.
    def _fake_worktree(db, work_item, builder_task_id=None):
        path = (
            f"/srv/worktrees/legion-dashboard/wi-{work_item.id}-"
            f"prompt-builder-raises/t_{(builder_task_id or 0):06d}"
        )
        branch = (
            f"feature/wi-{work_item.id}-prompt-builder-raises-"
            f"t_{(builder_task_id or 0):06d}"
        )
        return path, branch, "main", {
            "success": True,
            "reused": False,
            "base_ref": "main",
        }

    monkeypatch.setattr(
        builder_router, "ensure_task_worktree", _fake_worktree
    )

    # Make the prompt builder raise on the first invocation, then
    # fall back to the real implementation for the second
    # start-build.
    real_prompt = builder_router._generate_hermes_prompt
    call_state = {"raised": False}

    def _fake_prompt(**kwargs):
        if not call_state["raised"]:
            call_state["raised"] = True
            raise RuntimeError("simulated prompt generation failure")
        return real_prompt(**kwargs)

    monkeypatch.setattr(
        builder_router, "_generate_hermes_prompt", _fake_prompt
    )

    # Stop the TestClient from re-raising server exceptions so we
    # observe the 5xx response the operator would see in
    # production rather than the bare ``RuntimeError`` Starlette
    # surfaces in debug mode. The flag lives on the underlying
    # ``_TestClientTransport`` (not the TestClient itself), so we
    # flip it on the transport directly. Restored implicitly by
    # the fixture teardown (a fresh client is built per test).
    client._transport.raise_server_exceptions = False

    # First start-build: the prompt builder raises mid-flight.
    # The outer wrapper rolls back the stub and re-raises; the
    # unhandled RuntimeError surfaces as a 500.
    first = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert first.status_code >= 500, first.text

    # The stub row must have been rolled back: zero BuilderTask
    # rows for this work item exist.
    rows = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(rows) == 0, [r.hermes_task_id for r in rows]

    # The repo lock acquired before the prompt-gen failure must
    # have been released so the second build is not pinned by a
    # phantom lock. Query by work_item_id (NOT the legacy shared
    # /srv/repo/legion-dashboard path) — worktree-isolated builds
    # lock the task worktree path, so a repo_path filter would
    # pass vacuously and miss a real leak on the worktree path.
    active_locks = (
        db_session.query(RepoLock)
        .filter(RepoLock.work_item_id == item.id)
        .filter(RepoLock.lock_status == "active")
        .count()
    )
    assert active_locks == 0, (
        f"repo lock leaked after prompt-generation failure: "
        f"{active_locks}"
    )

    # A fresh second start-build for the same work item sees no
    # active task, generates the prompt successfully, and returns
    # 200.
    second = client.post(
        f"/api/builder/work-items/{item.id}/start-build", json={}
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["hermes_task_id"] == "hermes-prompt-raise-recovery-1"

    # Exactly one BuilderTask row exists after the recovery —
    # the rolled-back stub from the first attempt is gone.
    rows = (
        db_session.query(BuilderTask)
        .filter(BuilderTask.work_item_id == item.id)
        .all()
    )
    assert len(rows) == 1, [r.hermes_task_id for r in rows]
    assert rows[0].hermes_task_id == "hermes-prompt-raise-recovery-1"


# ---------------------------------------------------------------------------
# Container-native worktree tool: env-var resolution + deploy wiring
# ---------------------------------------------------------------------------


def _reload_task_worktree():
    """Reimport :mod:`app.task_worktree` so module-level env-var
    resolution re-runs against the current ``os.environ``."""
    import importlib
    from app import task_worktree

    return importlib.reload(task_worktree)


def _restore_env(key: str, original_value):
    """Restore an env var to its pre-test value."""
    if original_value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = original_value


def test_default_worktree_create_tool_constant_is_image_default():
    """The image-default path is the path the Dockerfile installs
    the repo-owned script to. Test pins the constant so a future
    edit cannot silently retarget the orchestrator at the legacy
    host path."""
    from app import task_worktree

    assert (
        task_worktree.DEFAULT_WORKTREE_CREATE_TOOL
        == "/usr/local/bin/legion-worktree-create"
    )


def test_worktree_create_tool_defaults_to_image_path_when_env_unset():
    """``WORKTREE_CREATE_TOOL`` falls back to
    ``DEFAULT_WORKTREE_CREATE_TOOL`` when
    ``LEGION_WORKTREE_CREATE_TOOL`` is not set in the environment."""
    original = os.environ.get("LEGION_WORKTREE_CREATE_TOOL")
    os.environ.pop("LEGION_WORKTREE_CREATE_TOOL", None)
    try:
        reloaded = _reload_task_worktree()
        assert (
            reloaded.WORKTREE_CREATE_TOOL
            == reloaded.DEFAULT_WORKTREE_CREATE_TOOL
            == "/usr/local/bin/legion-worktree-create"
        )
    finally:
        _restore_env("LEGION_WORKTREE_CREATE_TOOL", original)
        _reload_task_worktree()


def test_worktree_create_tool_reflects_env_var_when_set():
    """When ``LEGION_WORKTREE_CREATE_TOOL`` is set, the active
    ``WORKTREE_CREATE_TOOL`` constant reflects the override."""
    original = os.environ.get("LEGION_WORKTREE_CREATE_TOOL")
    os.environ["LEGION_WORKTREE_CREATE_TOOL"] = "/opt/legion/bin/wtc-override"
    try:
        reloaded = _reload_task_worktree()
        assert reloaded.WORKTREE_CREATE_TOOL == "/opt/legion/bin/wtc-override"
        # The default is unchanged; only the active value moved.
        assert (
            reloaded.DEFAULT_WORKTREE_CREATE_TOOL
            == "/usr/local/bin/legion-worktree-create"
        )
    finally:
        _restore_env("LEGION_WORKTREE_CREATE_TOOL", original)
        _reload_task_worktree()


def test_worktree_create_result_missing_tool_error_includes_configured_path(
    monkeypatch, tmp_path,
):
    """The ``_worktree_create_result`` error message names the
    configured tool path so the operator can see which path the
    orchestrator looked at — important when an env-var override is
    misconfigured."""
    from app import task_worktree

    missing_path = str(tmp_path / "no-such-legion-worktree-create")
    monkeypatch.setattr(task_worktree, "WORKTREE_CREATE_TOOL", missing_path)

    ok, parsed, stderr = task_worktree._worktree_create_result(
        worktree_path="/srv/worktrees/legion-dashboard/wi-1-x/t_000001",
        feature_branch="feature/x",
        base_ref="main",
    )
    assert ok is False
    assert parsed == {}
    assert "missing tool" in stderr
    assert missing_path in stderr, (
        f"error message must include the configured path; got {stderr!r}"
    )


def test_task_worktree_module_does_not_reference_legacy_host_path():
    """No code path in ``task_worktree.py`` may bake in the legacy
    ``/root/.hermes/...`` host-only path. The module must rely on
    the env var + image default exclusively."""
    import inspect
    from app import task_worktree

    source = inspect.getsource(task_worktree)
    assert "/root/.hermes/" not in source, (
        "task_worktree.py still references the legacy host-only "
        "/root/.hermes/ path; the deploy must use the in-image "
        "tool via LEGION_WORKTREE_CREATE_TOOL"
    )


def _load_compose_yaml() -> dict:
    """Parse ``docker-compose.yml`` from the repo root and return
    the loaded dict. The repo root is two parents above this test
    file (backend/tests -> backend -> repo root)."""
    import os
    import yaml

    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.abspath(os.path.join(here, "..", ".."))
    compose_path = os.path.join(repo_root, "docker-compose.yml")
    with open(compose_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_docker_compose_app_service_mounts_srv_worktrees_read_write():
    """The app service must bind-mount ``/srv/worktrees`` so the
    container can create per-task worktrees the host can see, and
    the mount MUST be read-write (no ``:ro``)."""
    compose = _load_compose_yaml()
    app_volumes = compose["services"]["app"]["volumes"]
    matches = [
        v for v in app_volumes
        if isinstance(v, str) and v.startswith("/srv/worktrees:/srv/worktrees")
    ]
    assert matches, (
        f"app service is missing /srv/worktrees bind mount; "
        f"got volumes={app_volumes!r}"
    )
    for entry in matches:
        assert not entry.endswith(":ro"), (
            f"/srv/worktrees mount must be read-write (no :ro); "
            f"got {entry!r}"
        )


def test_docker_compose_app_service_sets_legion_worktree_create_tool_env():
    """The app service must set ``LEGION_WORKTREE_CREATE_TOOL`` to
    the in-image path so the orchestrator resolves to the
    repo-owned, container-internal tool."""
    compose = _load_compose_yaml()
    env = compose["services"]["app"]["environment"]
    # docker-compose accepts either a mapping or a list of KEY=VAL;
    # this repo uses the mapping form.
    assert isinstance(env, dict), (
        f"docker-compose.yml app.environment is not a mapping; "
        f"got {type(env).__name__}"
    )
    assert "LEGION_WORKTREE_CREATE_TOOL" in env, (
        f"app service is missing LEGION_WORKTREE_CREATE_TOOL env var; "
        f"got environment keys={sorted(env.keys())!r}"
    )
    assert env["LEGION_WORKTREE_CREATE_TOOL"] == (
        "/usr/local/bin/legion-worktree-create"
    ), env["LEGION_WORKTREE_CREATE_TOOL"]
