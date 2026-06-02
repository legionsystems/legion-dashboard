"""Tests for the host-side preview executor + dashboard client (slice 4b).

Two surfaces are covered:

  * ``preview_deploy.call_host_executor`` — the dashboard's HTTP client.
    Exercised against a real loopback :class:`http.server.HTTPServer` so
    the urllib request, header, and response parsing paths run.
  * The host-side executor script at
    ``ops/host-executor/legion-preview-executor`` — imported as a module
    so the auth, validation, and action-runner units can be exercised
    without spawning a subprocess.

Together these prove the contract: the dashboard cannot mark a Work Item
"deployed" without the executor confirming success, the executor refuses
unauthorized callers, bad repos, and dirty worktrees, and lock release
happens on every failure path.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app import preview_deploy
from app.models import RepoLock, WorkItem


# ---------------------------------------------------------------------------
# Import the executor script as a module (the file lacks a .py suffix).
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXECUTOR_PATH = _REPO_ROOT / "ops" / "host-executor" / "legion-preview-executor"


def _load_executor_module():
    # The on-disk script has no ``.py`` suffix (it lives in ``$PATH``), so
    # ``spec_from_file_location`` would refuse to infer a loader. Pass an
    # explicit ``SourceFileLoader`` to load the file as a Python module.
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader("legion_preview_executor", str(_EXECUTOR_PATH))
    spec = importlib.util.spec_from_loader("legion_preview_executor", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def executor_module():
    return _load_executor_module()


# ---------------------------------------------------------------------------
# Loopback HTTP fixture for the call_host_executor client tests.
# ---------------------------------------------------------------------------


class _StubExecutor:
    """Minimal HTTP server that records requests and returns a configured reply."""

    def __init__(self):
        self.requests: list[dict] = []
        self.next_status = 200
        self.next_body: dict = {"success": True}
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        stub = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_POST(self):  # noqa: N802 (stdlib name)
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length > 0 else b""
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    payload = {"_raw": raw.decode("utf-8", errors="replace")}
                stub.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers.items()),
                        "json": payload,
                    }
                )
                body = json.dumps(stub.next_body).encode("utf-8")
                self.send_response(stub.next_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)

    @property
    def url(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://{host}:{port}/preview"


@pytest.fixture()
def stub_executor():
    server = _StubExecutor()
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# call_host_executor — wire-format tests
# ---------------------------------------------------------------------------


def test_call_host_executor_sends_api_key_header(stub_executor):
    stub_executor.next_body = {
        "success": True,
        "commit_sha": "c" * 40,
        "health_status": "healthy",
    }
    result = preview_deploy.call_host_executor(
        action="deploy_preview",
        repo_path="/srv/repo/legion-dashboard",
        branch="feature/x",
        api_key="test-key",
        url=stub_executor.url,
        timeout=10,
    )
    assert result.success is True
    assert result.commit_sha == "c" * 40
    assert result.health_status == "healthy"
    # Exactly one request, with the API key header and the expected body.
    assert len(stub_executor.requests) == 1
    req = stub_executor.requests[0]
    assert req["headers"].get("X-Api-Key") == "test-key"
    assert req["json"] == {
        "action": "deploy_preview",
        "repo_path": "/srv/repo/legion-dashboard",
        "branch": "feature/x",
    }


def test_call_host_executor_forwards_service_when_set(stub_executor):
    stub_executor.next_body = {"success": True}
    preview_deploy.call_host_executor(
        action="deploy_preview",
        repo_path="/srv/repo/legion-dashboard",
        branch="feature/x",
        service="app",
        api_key="k",
        url=stub_executor.url,
        timeout=10,
    )
    assert stub_executor.requests[0]["json"]["service"] == "app"


def test_call_host_executor_translates_http_500_to_failure(stub_executor):
    stub_executor.next_status = 500
    stub_executor.next_body = {
        "success": False,
        "error": "compose build failed",
        "error_code": "executor_compose_build_failed",
    }
    result = preview_deploy.call_host_executor(
        action="deploy_preview",
        repo_path="/srv/repo/legion-dashboard",
        branch="feature/x",
        api_key="k",
        url=stub_executor.url,
        timeout=10,
    )
    assert result.success is False
    assert result.error == "compose build failed"
    assert result.error_code == "executor_compose_build_failed"


def test_call_host_executor_unreachable_returns_failure():
    """Pointing at an unbound loopback port → success=False, unreachable code."""
    result = preview_deploy.call_host_executor(
        action="deploy_preview",
        repo_path="/srv/repo/legion-dashboard",
        branch="feature/x",
        api_key="k",
        # 127.0.0.1:1 is reserved and refuses connections.
        url="http://127.0.0.1:1/preview",
        timeout=2,
    )
    assert result.success is False
    assert result.error_code == "executor_unreachable"


def test_call_host_executor_non_json_response_returns_failure(stub_executor):
    # Override the handler's body with non-JSON via the next_body shim and
    # a custom encoder. Easiest path: pre-serialize the body as a string
    # the next_body would not naturally produce, then disable next_body
    # JSON-encoding by patching the server's response on the fly.
    stub_executor.next_body = {"unexpected": "structure"}
    # The stub always sends JSON, so simulate a non-JSON response by
    # configuring a status code with a body that *would* parse, but
    # whose top-level value is not a dict.
    stub_executor.next_body = ["not", "a", "dict"]
    result = preview_deploy.call_host_executor(
        action="deploy_preview",
        repo_path="/srv/repo/legion-dashboard",
        branch="feature/x",
        api_key="k",
        url=stub_executor.url,
        timeout=10,
    )
    assert result.success is False
    assert result.error_code == "executor_bad_response"


# ---------------------------------------------------------------------------
# Host-side executor — auth / validation
# ---------------------------------------------------------------------------


class _FakeHandler:
    """Captures send_response / headers / body so do_POST can be unit-tested."""

    class _Writer:
        def __init__(self):
            self.buffer = bytearray()

        def write(self, data: bytes) -> None:
            self.buffer.extend(data)

    def __init__(self, path: str, body: bytes, extra_headers: dict | None = None):
        from io import BytesIO

        self.path = path
        self.rfile = BytesIO(body)
        self.wfile = self._Writer()
        self.client_address = ("127.0.0.1", 12345)
        self.requestline = f"POST {path} HTTP/1.1"
        self.command = "POST"
        self.request_version = "HTTP/1.1"
        self.headers = {
            "Content-Length": str(len(body)),
            **(extra_headers or {}),
        }
        self.status_code: int | None = None
        self._sent_headers: dict = {}

    def send_response(self, status: int) -> None:
        self.status_code = status

    def send_header(self, key: str, value: str) -> None:
        self._sent_headers[key] = value

    def end_headers(self) -> None:
        pass

    def log_message(self, *args, **kwargs):
        pass

    def parsed_body(self) -> dict:
        return json.loads(bytes(self.wfile.buffer).decode("utf-8"))


def _invoke_do_post(executor_module, handler) -> None:
    executor_module.ExecutorHandler.do_POST(handler)


def test_executor_requires_api_key(executor_module):
    executor_module._set_api_key("secret-key")
    body = json.dumps(
        {
            "action": "deploy_preview",
            "repo_path": "/srv/repo/legion-dashboard",
            "branch": "feature/x",
        }
    ).encode("utf-8")
    handler = _FakeHandler("/preview", body, extra_headers={"X-Api-Key": "wrong-key"})
    _invoke_do_post(executor_module, handler)
    assert handler.status_code == 401
    assert handler.parsed_body()["error_code"] == "executor_unauthorized"


def test_executor_accepts_correct_api_key(executor_module, monkeypatch, tmp_path):
    """A valid key + valid payload reaches the action runner.

    Stub the runner so we don't shell out, just verify the body validation
    passes the request through.
    """
    executor_module._set_api_key("secret-key")
    allowlist = {str(tmp_path)}
    monkeypatch.setattr(executor_module, "ALLOWED_REPOS", allowlist)
    monkeypatch.setattr(
        executor_module,
        "_perform_action",
        lambda *args, **kw: {"success": True, "commit_sha": "z" * 40},
    )
    body = json.dumps(
        {
            "action": "deploy_preview",
            "repo_path": str(tmp_path),
            "branch": "feature/x",
        }
    ).encode("utf-8")
    handler = _FakeHandler("/preview", body, extra_headers={"X-Api-Key": "secret-key"})
    _invoke_do_post(executor_module, handler)
    assert handler.status_code == 200
    assert handler.parsed_body()["success"] is True


def test_executor_rejects_unknown_repo_path(executor_module):
    executor_module._set_api_key("secret-key")
    body = json.dumps(
        {
            "action": "deploy_preview",
            "repo_path": "/tmp/not-on-allowlist",
            "branch": "feature/x",
        }
    ).encode("utf-8")
    handler = _FakeHandler("/preview", body, extra_headers={"X-Api-Key": "secret-key"})
    _invoke_do_post(executor_module, handler)
    assert handler.status_code == 400
    assert handler.parsed_body()["error_code"] == "executor_bad_repo"


def test_executor_rejects_unknown_action(executor_module, monkeypatch, tmp_path):
    executor_module._set_api_key("secret-key")
    monkeypatch.setattr(executor_module, "ALLOWED_REPOS", {str(tmp_path)})
    body = json.dumps(
        {
            "action": "rm_rf",
            "repo_path": str(tmp_path),
            "branch": "main",
        }
    ).encode("utf-8")
    handler = _FakeHandler("/preview", body, extra_headers={"X-Api-Key": "secret-key"})
    _invoke_do_post(executor_module, handler)
    assert handler.status_code == 400
    assert handler.parsed_body()["error_code"] == "executor_bad_action"


def test_executor_rejects_branch_with_shell_metachar(
    executor_module, monkeypatch, tmp_path
):
    """Branch names with ``;`` / ``$`` / ``&`` etc must be refused."""
    executor_module._set_api_key("secret-key")
    monkeypatch.setattr(executor_module, "ALLOWED_REPOS", {str(tmp_path)})
    body = json.dumps(
        {
            "action": "deploy_preview",
            "repo_path": str(tmp_path),
            "branch": "feature/x; rm -rf /",
        }
    ).encode("utf-8")
    handler = _FakeHandler("/preview", body, extra_headers={"X-Api-Key": "secret-key"})
    _invoke_do_post(executor_module, handler)
    assert handler.status_code == 400
    assert handler.parsed_body()["error_code"] == "executor_bad_branch"


def test_executor_rejects_missing_repo_directory(
    executor_module, monkeypatch, tmp_path
):
    missing = tmp_path / "missing"
    executor_module._set_api_key("secret-key")
    monkeypatch.setattr(executor_module, "ALLOWED_REPOS", {str(missing)})
    body = json.dumps(
        {
            "action": "deploy_preview",
            "repo_path": str(missing),
            "branch": "main",
        }
    ).encode("utf-8")
    handler = _FakeHandler("/preview", body, extra_headers={"X-Api-Key": "secret-key"})
    _invoke_do_post(executor_module, handler)
    assert handler.status_code == 400
    assert handler.parsed_body()["error_code"] == "executor_repo_missing"


# ---------------------------------------------------------------------------
# Host-side executor — action runner against a real temp git repo
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def host_repo(tmp_path):
    repo = tmp_path / "host-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Tester")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_executor_blocks_dirty_repo(executor_module, host_repo):
    """Defense in depth: a dirty repo aborts even if the dashboard slipped past."""
    (host_repo / "README.md").write_text("dirty\n")
    result = executor_module._perform_action(
        "deploy_preview", str(host_repo), "main", "app"
    )
    assert result["success"] is False
    assert result["error_code"] == "executor_dirty_repo"


def test_executor_skip_compose_on_fetch_failure(executor_module, host_repo, monkeypatch):
    """A failing ``git fetch`` aborts before compose runs."""
    # Stub _run so fetch fails; spy on subsequent calls to confirm none run.
    calls: list[list[str]] = []

    def _fake_run(cmd, cwd, timeout):
        calls.append(list(cmd))
        if cmd[:3] == ["git", "-C", str(host_repo)] and "fetch" in cmd:
            return 1, "", "fatal: could not read from remote"
        # Diff / ls-files (clean check) should succeed.
        if "diff" in cmd or "ls-files" in cmd:
            return 0, "", ""
        # Anything else (compose) MUST NOT be reached.
        raise AssertionError(f"unexpected command after fetch failure: {cmd}")

    monkeypatch.setattr(executor_module, "_run", _fake_run)
    result = executor_module._perform_action(
        "deploy_preview", str(host_repo), "main", "app"
    )
    assert result["success"] is False
    assert result["error_code"] == "executor_git_fetch_failed"
    # The compose binary must not have been invoked.
    assert not any(cmd[:2] == ["docker", "compose"] for cmd in calls)


def test_executor_returns_commit_sha_on_success(executor_module, host_repo, monkeypatch):
    """A fully-stubbed happy path returns the post-checkout HEAD sha."""
    head_sha = _git(host_repo, "rev-parse", "HEAD").stdout.strip()

    def _fake_run(cmd, cwd, timeout):
        if "fetch" in cmd or "checkout" in cmd:
            return 0, "", ""
        if "diff" in cmd or "ls-files" in cmd:
            return 0, "", ""
        if "rev-parse" in cmd:
            return 0, head_sha + "\n", ""
        if cmd[:3] == ["docker", "compose", "build"]:
            return 0, "", ""
        if cmd[:4] == ["docker", "compose", "up", "-d"]:
            return 0, "", ""
        if cmd[:3] == ["docker", "compose", "ps"]:
            return 0, '{"Name": "app", "State": "running"}\n', ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(executor_module, "_run", _fake_run)
    result = executor_module._perform_action(
        "deploy_preview", str(host_repo), "main", "app"
    )
    assert result["success"] is True
    assert result["commit_sha"] == head_sha
    assert result["health_status"] == "healthy"


# ---------------------------------------------------------------------------
# End-to-end: dashboard router → real (loopback) executor server
# ---------------------------------------------------------------------------


@pytest.fixture()
def clean_repo(tmp_path):
    """A clean git repo that can satisfy the dashboard's repo_safety gate."""
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


def _seed_work_item(db) -> WorkItem:
    item = WorkItem(
        type="task",
        title="Preview deploy candidate",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        branch_name="feature/wi-preview",
        pr_number=42,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def test_router_marks_success_only_after_executor_acks(
    client, db_session, monkeypatch, clean_repo, stub_executor
):
    """The dashboard must stamp ``preview_deployed=True`` ONLY when the
    executor responded success — and never directly mutate /srv/repo."""
    item = _seed_work_item(db_session)
    monkeypatch.setattr(
        preview_deploy,
        "resolve_preview_repo",
        lambda _wi: (str(clean_repo), "legion-dashboard"),
    )
    # Point the dashboard at the real stub executor so the HTTP path runs.
    monkeypatch.setenv("LEGION_PREVIEW_EXECUTOR_URL", stub_executor.url)
    monkeypatch.setenv("API_SERVER_KEY", "shared-key")
    stub_executor.next_body = {
        "success": True,
        "commit_sha": "e" * 40,
        "health_status": "healthy",
    }

    response = client.post(
        f"/api/work-items/{item.id}/deploy-preview",
        json={"deployed_by": "operator-eve"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["preview_deployed"] is True
    assert body["preview_commit_sha"] == "e" * 40
    # The dashboard forwarded the configured key to the executor.
    assert len(stub_executor.requests) == 1
    assert stub_executor.requests[0]["headers"].get("X-Api-Key") == "shared-key"
    assert stub_executor.requests[0]["json"]["action"] == "deploy_preview"


def test_router_does_not_stamp_success_on_executor_failure(
    client, db_session, monkeypatch, clean_repo, stub_executor
):
    item = _seed_work_item(db_session)
    monkeypatch.setattr(
        preview_deploy,
        "resolve_preview_repo",
        lambda _wi: (str(clean_repo), "legion-dashboard"),
    )
    monkeypatch.setenv("LEGION_PREVIEW_EXECUTOR_URL", stub_executor.url)
    monkeypatch.setenv("API_SERVER_KEY", "shared-key")
    stub_executor.next_status = 500
    stub_executor.next_body = {
        "success": False,
        "error": "docker compose build failed (rc=1)",
        "error_code": "executor_compose_build_failed",
    }

    response = client.post(
        f"/api/work-items/{item.id}/deploy-preview", json={}
    )

    assert response.status_code == 500
    db_session.refresh(item)
    assert item.preview_deployed is not True
    assert item.preview_status == "error"
    lock = (
        db_session.query(RepoLock)
        .filter(RepoLock.repo_path == str(clean_repo))
        .one()
    )
    assert lock.lock_status == "failed"
