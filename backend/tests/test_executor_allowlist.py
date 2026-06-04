"""Tests for the executor allowlist UI surface.

Covers:

* Path validation (absolute, no ``..``, no symlink escape, no forbidden
  prefixes, duplicates rejected).
* CRUD: add / list / disable / re-enable / remove.
* Defaults are seeded (matching the executor's source defaults) and
  cannot be removed via the API; they can be disabled.
* Disabled defaults are excluded from the joined enabled list.
* ``apply`` writes the on-disk config file in the expected format, calls
  ``systemctl restart`` exactly once, and surfaces the restart result in
  the response + audit log.
* The legacy ``LEGION_EXECUTOR_ALLOWED_REPO_ROOTS`` env var continues to
  override the dashboard's persisted list (the executor reads the env;
  the dashboard surfaces that override on the GET response).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.models import ExecutorAllowlistApplyLog, ExecutorAllowlistRoot
from app.routers import executor_allowlist as allowlist_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_path(tmp_path, monkeypatch):
    """Point the apply-endpoint at a tmp file so the test suite never
    touches the real ``/etc/legion/`` path. Also stubs the restart command
    by default so a missing systemd binary on the test box can't cause
    flakes."""
    target = tmp_path / "executor-allowlist.conf"
    monkeypatch.setenv("LEGION_EXECUTOR_ALLOWLIST_CONFIG_PATH", str(target))
    return target


@pytest.fixture()
def stub_restart(monkeypatch):
    """Capture the systemctl restart command without actually running it."""
    calls: list[list[str]] = []

    def _fake(cmd, timeout=30):
        calls.append(list(cmd))
        return 0, "", ""

    monkeypatch.setattr(allowlist_router, "_run_restart", _fake)
    return calls


# ---------------------------------------------------------------------------
# Default seeding + GET
# ---------------------------------------------------------------------------


def test_get_seeds_defaults_on_first_read(client, db_session):
    """A fresh DB has no rows; GET back-fills the three baked-in
    defaults so the dashboard always matches the executor's source
    defaults out of the box."""
    assert db_session.query(ExecutorAllowlistRoot).count() == 0
    response = client.get("/api/executor-allowlist")
    assert response.status_code == 200, response.text
    body = response.json()
    paths = [r["path"] for r in body["roots"]]
    assert "/srv/repo/legion-dashboard" in paths
    assert "/srv/repo/lgn-hub" in paths
    assert "/srv/worktrees/legion-dashboard" in paths
    for root in body["roots"]:
        assert root["is_default"] is True
        assert root["is_enabled"] is True


def test_default_roots_match_executor_source(client):
    """The seeded defaults must match
    ``_DEFAULT_ALLOWED_REPO_ROOTS`` in ``legion-preview-executor`` — that
    is the whole point of seeding them."""
    assert set(allowlist_router.DEFAULT_ALLOWLIST_ROOTS) == {
        "/srv/repo/legion-dashboard",
        "/srv/repo/lgn-hub",
        "/srv/worktrees/legion-dashboard",
    }


def test_get_includes_config_path_and_override_state(client, config_path):
    response = client.get("/api/executor-allowlist")
    assert response.status_code == 200
    body = response.json()
    assert body["config_path"] == str(config_path)
    assert body["env_override_active"] is False
    assert body["env_override_value"] is None


def test_get_surfaces_env_override(client, monkeypatch):
    monkeypatch.setenv(
        "LEGION_EXECUTOR_ALLOWED_REPO_ROOTS",
        "/srv/repo/legion-dashboard,/custom/root",
    )
    response = client.get("/api/executor-allowlist")
    body = response.json()
    assert body["env_override_active"] is True
    assert body["env_override_value"] == "/srv/repo/legion-dashboard,/custom/root"


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_path",
    [
        "relative/path",
        "../escape",
        "/srv/repo/../etc/passwd",
        "/etc/legion",
        "/etc",
        "/root/private",
        "/tmp/anything",
        "/var/log/scratch",
        "/",
        "",
        "   ",
    ],
)
def test_add_rejects_invalid_path(client, bad_path):
    response = client.post(
        "/api/executor-allowlist", json={"path": bad_path}
    )
    assert response.status_code in (400, 422), (
        f"{bad_path!r} should have been rejected, got {response.status_code}"
    )


def test_add_rejects_symlink_escape(client, tmp_path, monkeypatch):
    """If the candidate path is a symlink that resolves elsewhere, refuse
    the request — even if the resolved location happens to be valid. The
    operator should be using the realpath.

    ``tmp_path`` lives under ``/tmp`` which is on the forbidden-prefix
    list; relax that for the duration of this test so the symlink check
    is the one that fires.
    """
    target = tmp_path / "real-repo"
    target.mkdir()
    link = tmp_path / "linked-repo"
    link.symlink_to(target)
    monkeypatch.setattr(allowlist_router, "_FORBIDDEN_PREFIXES", ())
    response = client.post(
        "/api/executor-allowlist", json={"path": str(link)}
    )
    assert response.status_code == 400
    assert "symlink" in response.json()["detail"]


def test_add_rejects_duplicate(client):
    # Seed defaults exist already after GET.
    client.get("/api/executor-allowlist")
    response = client.post(
        "/api/executor-allowlist",
        json={"path": "/srv/repo/legion-dashboard"},
    )
    assert response.status_code == 409


def test_add_canonicalizes_redundant_slashes(client):
    response = client.post(
        "/api/executor-allowlist",
        json={"path": "/srv/repo/legion-new-app/"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["path"] == "/srv/repo/legion-new-app"
    assert body["is_default"] is False
    assert body["is_enabled"] is True


# ---------------------------------------------------------------------------
# CRUD: add / list / disable / re-enable / remove
# ---------------------------------------------------------------------------


def test_add_then_list_returns_new_row(client):
    client.get("/api/executor-allowlist")  # seed
    add = client.post(
        "/api/executor-allowlist",
        json={
            "path": "/srv/repo/legion-newapp",
            "note": "onboarding lgn-newapp",
            "added_by": "operator-eve",
        },
    )
    assert add.status_code == 201, add.text
    listed = client.get("/api/executor-allowlist").json()
    paths = [r["path"] for r in listed["roots"]]
    assert "/srv/repo/legion-newapp" in paths
    new_row = next(
        r for r in listed["roots"] if r["path"] == "/srv/repo/legion-newapp"
    )
    assert new_row["note"] == "onboarding lgn-newapp"
    assert new_row["added_by"] == "operator-eve"
    assert new_row["is_default"] is False


def test_patch_disables_root(client):
    client.get("/api/executor-allowlist")
    add = client.post(
        "/api/executor-allowlist", json={"path": "/srv/repo/lgn-extra"}
    ).json()
    patched = client.patch(
        f"/api/executor-allowlist/{add['id']}",
        json={"is_enabled": False, "note": "paused"},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["is_enabled"] is False
    assert body["note"] == "paused"


def test_patch_can_disable_default(client, db_session):
    """Defaults can be disabled (operator may want to drop /srv/repo/lgn-hub
    on a single-app host) — they just cannot be deleted."""
    client.get("/api/executor-allowlist")
    default = (
        db_session.query(ExecutorAllowlistRoot)
        .filter(ExecutorAllowlistRoot.path == "/srv/repo/lgn-hub")
        .one()
    )
    response = client.patch(
        f"/api/executor-allowlist/{default.id}",
        json={"is_enabled": False},
    )
    assert response.status_code == 200, response.text
    assert response.json()["is_enabled"] is False


def test_delete_non_default_removes_row(client):
    client.get("/api/executor-allowlist")
    add = client.post(
        "/api/executor-allowlist", json={"path": "/srv/repo/lgn-extra"}
    ).json()
    delete = client.delete(f"/api/executor-allowlist/{add['id']}")
    assert delete.status_code == 204
    listed = client.get("/api/executor-allowlist").json()
    paths = [r["path"] for r in listed["roots"]]
    assert "/srv/repo/lgn-extra" not in paths


def test_delete_default_is_refused(client, db_session):
    client.get("/api/executor-allowlist")
    default = (
        db_session.query(ExecutorAllowlistRoot)
        .filter(ExecutorAllowlistRoot.path == "/srv/repo/legion-dashboard")
        .one()
    )
    response = client.delete(f"/api/executor-allowlist/{default.id}")
    assert response.status_code == 409
    assert "default" in response.json()["detail"]
    # The row is still there.
    assert (
        db_session.query(ExecutorAllowlistRoot)
        .filter(ExecutorAllowlistRoot.id == default.id)
        .one_or_none()
        is not None
    )


def test_patch_missing_id_returns_404(client):
    response = client.patch(
        "/api/executor-allowlist/99999", json={"is_enabled": False}
    )
    assert response.status_code == 404


def test_delete_missing_id_returns_404(client):
    response = client.delete("/api/executor-allowlist/99999")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Pending-apply marker
# ---------------------------------------------------------------------------


def test_pending_apply_is_true_when_list_changes(client, config_path):
    # No on-disk file yet → pending should already be True (defaults
    # exist in DB but nothing has been applied).
    body = client.get("/api/executor-allowlist").json()
    assert body["pending_apply"] is True


# ---------------------------------------------------------------------------
# Apply endpoint
# ---------------------------------------------------------------------------


def test_apply_requires_confirm(client, config_path, stub_restart):
    response = client.post("/api/executor-allowlist/apply", json={"confirm": False})
    assert response.status_code == 400
    # And no restart was attempted.
    assert stub_restart == []


def test_apply_writes_config_file_in_expected_format(
    client, config_path, stub_restart
):
    client.get("/api/executor-allowlist")  # seed defaults
    response = client.post(
        "/api/executor-allowlist/apply",
        json={"confirm": True, "applied_by": "operator-eve"},
    )
    assert response.status_code == 200, response.text
    contents = Path(config_path).read_text(encoding="utf-8")

    # The header is a block of comment lines (one per enabled root, with a
    # ``[default]`` tag for the seeded entries) followed by a single
    # ``LEGION_EXECUTOR_ALLOWED_REPO_ROOTS=`` assignment.
    lines = contents.splitlines()
    comment_path_lines = [
        ln for ln in lines if ln.startswith("# /srv/")
    ]
    assert len(comment_path_lines) == 3
    for path in (
        "/srv/repo/legion-dashboard",
        "/srv/repo/lgn-hub",
        "/srv/worktrees/legion-dashboard",
    ):
        assert any(
            ln.startswith(f"# {path}") and "[default]" in ln
            for ln in comment_path_lines
        ), f"missing default tag for {path}"

    assignment_lines = [
        ln for ln in lines if ln.startswith("LEGION_EXECUTOR_ALLOWED_REPO_ROOTS=")
    ]
    assert len(assignment_lines) == 1
    csv = assignment_lines[0].split("=", 1)[1]
    assert csv == (
        "/srv/repo/legion-dashboard,/srv/repo/lgn-hub,/srv/worktrees/legion-dashboard"
    )


def test_apply_calls_systemctl_restart_exactly_once(
    client, config_path, stub_restart
):
    client.get("/api/executor-allowlist")
    client.post(
        "/api/executor-allowlist/apply", json={"confirm": True}
    )
    assert len(stub_restart) == 1
    cmd = stub_restart[0]
    assert cmd[-1] == "legion-preview-executor"
    assert "restart" in cmd
    assert cmd[0].endswith("systemctl") or cmd[0] == "systemctl"


def test_apply_records_audit_row(client, config_path, stub_restart, db_session):
    client.get("/api/executor-allowlist")
    client.post(
        "/api/executor-allowlist/apply",
        json={"confirm": True, "applied_by": "operator-eve"},
    )
    audit = (
        db_session.query(ExecutorAllowlistApplyLog)
        .order_by(ExecutorAllowlistApplyLog.applied_at.desc())
        .first()
    )
    assert audit is not None
    assert audit.applied_by == "operator-eve"
    assert audit.restart_ok is True
    assert audit.error is None
    assert "/srv/repo/legion-dashboard" in audit.joined_roots


def test_apply_clears_pending_apply_marker(client, config_path, stub_restart):
    client.get("/api/executor-allowlist")
    client.post("/api/executor-allowlist/apply", json={"confirm": True})
    body = client.get("/api/executor-allowlist").json()
    assert body["pending_apply"] is False
    assert body["last_apply_restart_ok"] is True
    assert body["last_apply_error"] is None


def test_apply_surfaces_restart_failure(
    client, config_path, monkeypatch, db_session
):
    """A non-zero systemctl exit must be recorded on the audit row + the
    response, but the on-disk write still happens (so a follow-up
    operator action can recover)."""
    def _fail(cmd, timeout=30):
        return 1, "", "Unit not found"

    monkeypatch.setattr(allowlist_router, "_run_restart", _fail)
    client.get("/api/executor-allowlist")
    response = client.post(
        "/api/executor-allowlist/apply", json={"confirm": True}
    ).json()
    assert response["last_apply_restart_ok"] is False
    assert "Unit not found" in (response["last_apply_error"] or "")


def test_disabled_default_excluded_from_joined_list(
    client, config_path, stub_restart, db_session
):
    """Disabling a default removes it from the CSV the executor will
    actually read on restart — this is the one operator lever for
    declining a baked-in default."""
    client.get("/api/executor-allowlist")
    default = (
        db_session.query(ExecutorAllowlistRoot)
        .filter(ExecutorAllowlistRoot.path == "/srv/repo/lgn-hub")
        .one()
    )
    client.patch(
        f"/api/executor-allowlist/{default.id}", json={"is_enabled": False}
    )
    client.post("/api/executor-allowlist/apply", json={"confirm": True})
    contents = Path(config_path).read_text(encoding="utf-8")
    assignment = next(
        ln for ln in contents.splitlines()
        if ln.startswith("LEGION_EXECUTOR_ALLOWED_REPO_ROOTS=")
    )
    csv = assignment.split("=", 1)[1].split(",")
    assert "/srv/repo/lgn-hub" not in csv
    assert "/srv/repo/legion-dashboard" in csv


def test_apply_is_atomic(client, config_path, stub_restart):
    """Two applies in a row should leave no .tmp lying around."""
    client.get("/api/executor-allowlist")
    client.post("/api/executor-allowlist/apply", json={"confirm": True})
    client.post("/api/executor-allowlist/apply", json={"confirm": True})
    tmp = Path(str(config_path) + ".tmp")
    assert not tmp.exists()


# ---------------------------------------------------------------------------
# Env-var override precedence (documented contract — the env wins)
# ---------------------------------------------------------------------------


def test_env_override_takes_precedence_on_executor(monkeypatch):
    """The dashboard surfaces the env override but the executor itself is
    what honors it. This test guards the contract by importing the
    executor module and asserting its loader prefers the env when set —
    so the README's documented precedence stays true."""
    import importlib.util
    from importlib.machinery import SourceFileLoader

    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "ops" / "host-executor" / "legion-preview-executor"
    loader = SourceFileLoader("legion_preview_executor_for_env_test", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    monkeypatch.setenv(
        "LEGION_EXECUTOR_ALLOWED_REPO_ROOTS", "/custom/root,/another/root"
    )
    roots = module._load_allowed_repo_roots()
    assert roots == ["/custom/root", "/another/root"]


# ---------------------------------------------------------------------------
# seed_default_allowlist_roots is idempotent
# ---------------------------------------------------------------------------


def test_seed_default_allowlist_roots_is_idempotent(db_session):
    allowlist_router.seed_default_allowlist_roots(db_session)
    allowlist_router.seed_default_allowlist_roots(db_session)
    rows = (
        db_session.query(ExecutorAllowlistRoot)
        .filter(ExecutorAllowlistRoot.is_default == True)  # noqa: E712
        .all()
    )
    assert len(rows) == 3
