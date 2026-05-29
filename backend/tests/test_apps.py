"""Tests for the apps operations router.

Subprocess execution is replaced by an in-memory fake runner so no docker
commands are ever issued. Tests verify allowlist enforcement, action logging,
status transitions, and structured result classification for cases the UI must
present cleanly (compose-file missing, logs before first start, pull on a
build-only app, timeouts).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Sequence, Tuple

import pytest

from app.models import App, AppActionLog
from app.routers import apps as apps_router


@dataclass
class FakeResult:
    exit_code: int = 0
    stdout: str = "ok"
    stderr: str = ""


class FakeRunner:
    """Records every runner invocation and returns a queued result.

    A single `default` result is returned for every call unless `script` is
    supplied, in which case each call pops the next result. This lets tests
    distinguish between the `ps -q` probe and the action call without
    coupling to argv shape.
    """

    def __init__(
        self,
        default: FakeResult | None = None,
        script: List[FakeResult] | None = None,
    ):
        self.default = default or FakeResult()
        self.script = list(script) if script else None
        self.calls: List[Tuple[Tuple[str, ...], str | None]] = []

    def __call__(
        self, argv: Sequence[str], cwd: str | None
    ) -> apps_router.RunResult:
        self.calls.append((tuple(argv), cwd))
        if self.script:
            result = self.script.pop(0)
        else:
            result = self.default
        return apps_router.RunResult(
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        )


@pytest.fixture()
def fake_runner() -> Iterator[FakeRunner]:
    runner = FakeRunner()
    apps_router.set_action_runner(runner)
    yield runner
    apps_router.reset_action_runner()


def _write_compose(
    dir_: Path,
    *,
    image: bool = True,
    build: bool = False,
    extra: str = "",
) -> Path:
    """Drop a minimal compose file on disk. `image`/`build` toggle whether
    the synthesised service declares `image:` and/or `build:` so tests can
    drive the build-only detection."""
    dir_.mkdir(parents=True, exist_ok=True)
    path = dir_ / "docker-compose.yml"
    lines = ["services:", "  web:"]
    if image:
        lines.append("    image: example/web:latest")
    if build:
        lines.append("    build: .")
    if not (image or build):
        # Compose requires *something* per service; a no-op command is fine
        # for our static inspection.
        lines.append("    command: ['true']")
    if extra:
        lines.append(extra)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _seed_app(
    db_session,
    app_id: str = "test-app",
    compose_path: str | None = None,
    *,
    tmp_path: Path | None = None,
    image: bool = True,
    build: bool = False,
) -> int:
    """Seed an app row and, unless compose_path is explicitly given, create
    a backing compose file under tmp_path so router checks don't short-circuit
    on `not_found`."""
    if compose_path is None:
        assert tmp_path is not None, "tmp_path required when compose_path omitted"
        path = _write_compose(tmp_path / app_id, image=image, build=build)
        compose_path = str(path)
    row = App(
        app_id=app_id,
        name=f"Test {app_id}",
        repo=str(Path(compose_path).parent),
        compose_project=app_id,
        compose_path=compose_path,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row.id


# ---------- listing / retrieval -----------------------------------------


def test_list_apps_empty(client):
    response = client.get("/api/apps")
    assert response.status_code == 200
    assert response.json() == []


def test_list_apps_returns_seeded(client, db_session, tmp_path):
    _seed_app(db_session, "alpha", tmp_path=tmp_path)
    _seed_app(db_session, "beta", tmp_path=tmp_path)
    response = client.get("/api/apps")
    assert response.status_code == 200
    ids = [a["app_id"] for a in response.json()]
    assert ids == ["alpha", "beta"]


def test_get_app(client, db_session, tmp_path):
    _seed_app(db_session, "gamma", tmp_path=tmp_path)
    response = client.get("/api/apps/gamma")
    assert response.status_code == 200
    body = response.json()
    assert body["app_id"] == "gamma"
    assert body["status"] == "unknown"
    # Image-only compose should not be flagged as build-only.
    assert body["compose_exists"] is True
    assert body["build_only"] is False


def test_get_app_marks_build_only(client, db_session, tmp_path):
    _seed_app(db_session, "buildy", tmp_path=tmp_path, image=False, build=True)
    response = client.get("/api/apps/buildy")
    assert response.status_code == 200
    body = response.json()
    assert body["build_only"] is True
    assert body["compose_exists"] is True


def test_get_app_marks_missing_compose(client, db_session):
    _seed_app(
        db_session,
        "ghost",
        compose_path="/tmp/legion-dashboard-tests/no-such/docker-compose.yml",
    )
    response = client.get("/api/apps/ghost")
    assert response.status_code == 200
    body = response.json()
    assert body["compose_exists"] is False
    assert body["build_only"] is None


def test_get_app_not_found(client):
    response = client.get("/api/apps/no-such-app")
    assert response.status_code == 404


# ---------- action allowlist & validation -------------------------------


def test_execute_action_rejects_unknown_action(client, db_session, fake_runner, tmp_path):
    _seed_app(db_session, "delta", tmp_path=tmp_path)
    response = client.post(
        "/api/apps/delta/action", json={"action": "rm -rf /"}
    )
    assert response.status_code == 400
    assert "not allowed" in response.json()["detail"]
    assert fake_runner.calls == []  # never invoked


def test_execute_action_rejects_unknown_app(client, fake_runner):
    response = client.post(
        "/api/apps/nope/action", json={"action": "start"}
    )
    assert response.status_code == 404
    assert fake_runner.calls == []


def test_execute_action_argv_has_no_shell_metacharacters(
    client, db_session, fake_runner, tmp_path
):
    """Regression guard: every argv element must come from the allowlist or
    a DB row whose contents are reconciled from apps_config — never from the
    request body. We assert no element contains shell metacharacters."""
    _seed_app(db_session, "safe", tmp_path=tmp_path)
    client.post("/api/apps/safe/action", json={"action": "start"})
    assert fake_runner.calls, "runner should have been invoked"
    for argv, _ in fake_runner.calls:
        for piece in argv:
            assert "&" not in piece
            assert ";" not in piece
            assert "|" not in piece
            assert "`" not in piece
            assert "$(" not in piece


# ---------- happy path --------------------------------------------------


def test_execute_action_start_success(client, db_session, fake_runner, tmp_path):
    _seed_app(db_session, "epsilon", tmp_path=tmp_path)
    response = client.post(
        "/api/apps/epsilon/action", json={"action": "start"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["app"]["status"] == "running"
    assert body["app"]["last_action"] == "start"
    assert body["app"]["last_result"] == "success"
    assert body["log"]["action"] == "start"
    assert body["log"]["result"] == "success"
    assert body["log"]["exit_code"] == 0
    assert body["log"]["message"]

    # Argv must contain the compose path from the DB, no shell metacharacters.
    assert len(fake_runner.calls) == 1
    argv, cwd = fake_runner.calls[0]
    assert argv[0:2] == ("docker", "compose")
    assert "-f" in argv
    assert "-p" in argv
    assert "epsilon" in argv
    assert "up" in argv and "-d" in argv
    assert cwd == str(tmp_path / "epsilon")


def test_execute_action_stop_sets_stopped(client, db_session, fake_runner, tmp_path):
    _seed_app(db_session, "zeta", tmp_path=tmp_path)
    response = client.post("/api/apps/zeta/action", json={"action": "stop"})
    assert response.status_code == 200
    assert response.json()["app"]["status"] == "stopped"


def test_execute_action_rebuild_includes_build_flag(
    client, db_session, fake_runner, tmp_path
):
    _seed_app(db_session, "eta", tmp_path=tmp_path)
    response = client.post(
        "/api/apps/eta/action", json={"action": "rebuild"}
    )
    assert response.status_code == 200
    argv, _ = fake_runner.calls[-1]
    assert "--build" in argv


# ---------- structured failure modes ------------------------------------


def test_execute_action_failure_records_failed(client, db_session, tmp_path):
    _seed_app(db_session, "theta", tmp_path=tmp_path)
    failing = FakeRunner(FakeResult(exit_code=1, stdout="", stderr="boom"))
    apps_router.set_action_runner(failing)
    try:
        response = client.post(
            "/api/apps/theta/action", json={"action": "start"}
        )
    finally:
        apps_router.reset_action_runner()
    assert response.status_code == 200
    body = response.json()
    assert body["app"]["last_result"] == "failed"
    # Status should not jump to `running` on a failed start.
    assert body["app"]["status"] != "running"
    assert body["log"]["result"] == "failed"
    assert body["log"]["exit_code"] == 1
    assert body["log"]["stderr_tail"] == "boom"
    # Friendly UI message — should not be the raw "exit=-1" string.
    assert body["log"]["message"]
    assert "boom" in body["log"]["message"]


def test_execute_action_missing_compose_returns_not_found(client, db_session):
    _seed_app(
        db_session,
        "phantom",
        compose_path="/tmp/legion-dashboard-tests/missing/docker-compose.yml",
    )
    no_run = FakeRunner()
    apps_router.set_action_runner(no_run)
    try:
        response = client.post(
            "/api/apps/phantom/action", json={"action": "start"}
        )
    finally:
        apps_router.reset_action_runner()
    assert response.status_code == 200
    body = response.json()
    assert body["log"]["result"] == "not_found"
    assert body["app"]["last_result"] == "not_found"
    assert "Compose file not found" in body["log"]["message"]
    # Runner must NOT have been invoked for missing compose files.
    assert no_run.calls == []


def test_pull_on_build_only_app_is_not_applicable(client, db_session, tmp_path):
    _seed_app(db_session, "buildy", tmp_path=tmp_path, image=False, build=True)
    no_run = FakeRunner()
    apps_router.set_action_runner(no_run)
    try:
        response = client.post(
            "/api/apps/buildy/action", json={"action": "pull"}
        )
    finally:
        apps_router.reset_action_runner()
    assert response.status_code == 200
    body = response.json()
    assert body["log"]["result"] == "not_applicable"
    assert body["app"]["last_result"] == "not_applicable"
    assert body["app"]["build_only"] is True
    assert "not applicable" in body["log"]["message"].lower()
    assert no_run.calls == []


def test_logs_action_on_non_running_app_is_not_running(
    client, db_session, tmp_path
):
    _seed_app(db_session, "snore", tmp_path=tmp_path)
    # ps -q returns empty stdout with exit 0 → no containers.
    silent = FakeRunner(FakeResult(exit_code=0, stdout="", stderr=""))
    apps_router.set_action_runner(silent)
    try:
        response = client.post(
            "/api/apps/snore/action", json={"action": "logs"}
        )
    finally:
        apps_router.reset_action_runner()
    assert response.status_code == 200
    body = response.json()
    assert body["log"]["result"] == "not_running"
    assert "not running" in body["log"]["message"].lower()
    # Only the ps probe should have been invoked, never `logs`.
    assert len(silent.calls) == 1
    argv, _ = silent.calls[0]
    assert "ps" in argv and "-q" in argv


def test_execute_action_timeout_is_classified(client, db_session, tmp_path):
    _seed_app(db_session, "slow", tmp_path=tmp_path)

    class TimeoutRunner:
        calls: List[Tuple[Tuple[str, ...], str | None]] = []

        def __call__(self, argv, cwd):
            self.calls.append((tuple(argv), cwd))
            raise subprocess.TimeoutExpired(cmd=list(argv), timeout=42)

    apps_router.set_action_runner(TimeoutRunner())
    try:
        response = client.post(
            "/api/apps/slow/action", json={"action": "start"}
        )
    finally:
        apps_router.reset_action_runner()
    assert response.status_code == 200
    body = response.json()
    assert body["log"]["result"] == "timeout"
    assert body["app"]["last_result"] == "timeout"
    assert "timed out" in body["log"]["message"].lower()


# ---------- action log persistence --------------------------------------


def test_execute_action_persists_log(client, db_session, fake_runner, tmp_path):
    _seed_app(db_session, "iota", tmp_path=tmp_path)
    client.post("/api/apps/iota/action", json={"action": "pull"})
    client.post("/api/apps/iota/action", json={"action": "restart"})

    response = client.get("/api/apps/iota/action-logs")
    assert response.status_code == 200
    logs = response.json()
    assert len(logs) == 2
    actions = [log["action"] for log in logs]
    assert "pull" in actions and "restart" in actions


# ---------- logs endpoint -----------------------------------------------


def test_logs_endpoint_returns_lines(client, db_session, tmp_path):
    _seed_app(db_session, "kappa", tmp_path=tmp_path)
    # ps probe returns at least one container id so we proceed to logs.
    runner = FakeRunner(
        script=[
            FakeResult(exit_code=0, stdout="abcdef123\n", stderr=""),
            FakeResult(
                exit_code=0,
                stdout="line-1\nline-2\nline-3\n",
                stderr="",
            ),
        ]
    )
    apps_router.set_action_runner(runner)
    try:
        response = client.get("/api/apps/kappa/logs?tail=10")
    finally:
        apps_router.reset_action_runner()
    assert response.status_code == 200
    body = response.json()
    assert body["app_id"] == "kappa"
    assert body["lines"] == ["line-1", "line-2", "line-3"]
    assert body["result"] == "success"


def test_logs_endpoint_handles_not_running(client, db_session, tmp_path):
    _seed_app(db_session, "mu", tmp_path=tmp_path)
    runner = FakeRunner(FakeResult(exit_code=0, stdout="", stderr=""))
    apps_router.set_action_runner(runner)
    try:
        response = client.get("/api/apps/mu/logs")
    finally:
        apps_router.reset_action_runner()
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "not_running"
    assert body["lines"] == []
    assert body["message"]


def test_logs_endpoint_handles_missing_compose(client, db_session):
    _seed_app(
        db_session,
        "nu",
        compose_path="/tmp/legion-dashboard-tests/nope/docker-compose.yml",
    )
    no_run = FakeRunner()
    apps_router.set_action_runner(no_run)
    try:
        response = client.get("/api/apps/nu/logs")
    finally:
        apps_router.reset_action_runner()
    assert response.status_code == 200
    body = response.json()
    assert body["result"] == "not_found"
    assert body["lines"] == []
    assert no_run.calls == []


# ---------- allowlist coverage ------------------------------------------


def test_all_allowlisted_actions_are_accepted(
    client, db_session, fake_runner, tmp_path
):
    """Smoke test: every allowlisted action returns 200 with a recognised
    structured result. None should produce raw `exit=-1` strings the UI used
    to display."""
    _seed_app(db_session, "lambda-app", tmp_path=tmp_path)
    valid_results = {
        "success",
        "not_running",
        "not_found",
        "not_applicable",
        "not_configured",
        "failed",
        "timeout",
    }
    for action in apps_router.ALLOWED_ACTIONS:
        response = client.post(
            "/api/apps/lambda-app/action", json={"action": action}
        )
        assert response.status_code == 200, f"{action}: {response.text}"
        body = response.json()
        assert body["log"]["result"] in valid_results, (
            f"{action} produced unrecognised result {body['log']['result']!r}"
        )


def test_execute_action_returns_not_configured_when_docker_unavailable(
    client, db_session, tmp_path, monkeypatch
):
    """Verify that when docker CLI is unavailable, actions return not_configured."""
    _seed_app(db_session, "omega", tmp_path=tmp_path)

    # Patch shutil.which to simulate missing docker CLI
    monkeypatch.setattr("shutil.which", lambda name: None if name == "docker" else "/usr/bin/other")

    response = client.post(
        "/api/apps/omega/action", json={"action": "start"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["log"]["result"] == "not_configured"
    assert body["app"]["last_result"] == "not_configured"
    assert "Docker CLI not found" in body["log"]["message"]
