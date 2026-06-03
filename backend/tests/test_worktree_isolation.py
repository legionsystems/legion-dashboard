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
        worktree_path, feature_branch, result = ensure_task_worktree(db, wi)
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
    _worktree_path2, _branch2, result2 = ensure_task_worktree(db, wi)
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
        target_worktree, feature_branch, _result = ensure_task_worktree(
            db_session, wi, builder_task_id=1
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
        first_path, first_branch, _ = ensure_task_worktree(
            db_session, wi, builder_task_id=1
        )
        second_path, second_branch, _ = ensure_task_worktree(
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
