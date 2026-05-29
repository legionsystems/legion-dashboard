"""Tests for the apps operations router.

Subprocess execution is replaced by an in-memory fake runner so no docker
commands are ever issued. Tests verify allowlist enforcement, action logging,
and status transitions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import pytest

from app.models import App, AppActionLog
from app.routers import apps as apps_router


@dataclass
class FakeResult:
    exit_code: int = 0
    stdout: str = "ok"
    stderr: str = ""


class FakeRunner:
    def __init__(self, result: FakeResult | None = None):
        self.result = result or FakeResult()
        self.calls: List[Tuple[Tuple[str, ...], str | None]] = []

    def __call__(self, argv: Sequence[str], cwd: str | None) -> apps_router.RunResult:
        self.calls.append((tuple(argv), cwd))
        return apps_router.RunResult(
            exit_code=self.result.exit_code,
            stdout=self.result.stdout,
            stderr=self.result.stderr,
        )


@pytest.fixture()
def fake_runner():
    runner = FakeRunner()
    apps_router.set_action_runner(runner)
    yield runner
    apps_router.reset_action_runner()


def _seed_app(
    db_session,
    app_id: str = "test-app",
    compose_path: str = "/tmp/test-app/docker-compose.yml",
):
    row = App(
        app_id=app_id,
        name=f"Test {app_id}",
        repo=f"/tmp/{app_id}",
        compose_project=app_id,
        compose_path=compose_path,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row.id


def test_list_apps_empty(client):
    response = client.get("/api/apps")
    assert response.status_code == 200
    assert response.json() == []


def test_list_apps_returns_seeded(client, db_session):
    _seed_app(db_session, "alpha")
    _seed_app(db_session, "beta")
    response = client.get("/api/apps")
    assert response.status_code == 200
    ids = [a["app_id"] for a in response.json()]
    assert ids == ["alpha", "beta"]


def test_get_app(client, db_session):
    _seed_app(db_session, "gamma")
    response = client.get("/api/apps/gamma")
    assert response.status_code == 200
    body = response.json()
    assert body["app_id"] == "gamma"
    assert body["status"] == "unknown"


def test_get_app_not_found(client):
    response = client.get("/api/apps/no-such-app")
    assert response.status_code == 404


def test_execute_action_rejects_unknown_action(client, db_session, fake_runner):
    _seed_app(db_session, "delta")
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


def test_execute_action_start_success(client, db_session, fake_runner):
    _seed_app(
        db_session, "epsilon", compose_path="/tmp/epsilon/docker-compose.yml"
    )
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

    # Argv must contain the compose path from the DB, no shell metacharacters.
    assert len(fake_runner.calls) == 1
    argv, cwd = fake_runner.calls[0]
    assert argv[0:2] == ("docker", "compose")
    assert "-f" in argv
    assert "/tmp/epsilon/docker-compose.yml" in argv
    assert "-p" in argv
    assert "epsilon" in argv
    assert "up" in argv and "-d" in argv
    assert cwd == "/tmp/epsilon"


def test_execute_action_stop_sets_stopped(client, db_session, fake_runner):
    _seed_app(db_session, "zeta")
    response = client.post("/api/apps/zeta/action", json={"action": "stop"})
    assert response.status_code == 200
    assert response.json()["app"]["status"] == "stopped"


def test_execute_action_rebuild_includes_build_flag(client, db_session, fake_runner):
    _seed_app(db_session, "eta")
    response = client.post(
        "/api/apps/eta/action", json={"action": "rebuild"}
    )
    assert response.status_code == 200
    argv, _ = fake_runner.calls[-1]
    assert "--build" in argv


def test_execute_action_failure_records_failed(client, db_session):
    _seed_app(db_session, "theta")
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
    assert body["app"]["status"] == "unknown"
    assert body["log"]["result"] == "failed"
    assert body["log"]["exit_code"] == 1
    assert body["log"]["stderr_tail"] == "boom"


def test_execute_action_persists_log(client, db_session, fake_runner):
    _seed_app(db_session, "iota")
    client.post("/api/apps/iota/action", json={"action": "pull"})
    client.post("/api/apps/iota/action", json={"action": "restart"})

    response = client.get("/api/apps/iota/action-logs")
    assert response.status_code == 200
    logs = response.json()
    assert len(logs) == 2
    actions = [log["action"] for log in logs]
    assert "pull" in actions and "restart" in actions


def test_logs_endpoint_returns_lines(client, db_session):
    _seed_app(db_session, "kappa")
    runner = FakeRunner(
        FakeResult(exit_code=0, stdout="line-1\nline-2\nline-3\n", stderr="")
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


def test_all_allowlisted_actions_are_accepted(client, db_session, fake_runner):
    _seed_app(db_session, "lambda-app")
    for action in apps_router.ALLOWED_ACTIONS:
        response = client.post(
            "/api/apps/lambda-app/action", json={"action": action}
        )
        assert response.status_code == 200, f"{action}: {response.text}"
