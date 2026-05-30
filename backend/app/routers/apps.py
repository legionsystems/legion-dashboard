"""Apps operations router.

Safety contract:
- The action name MUST be one of `ALLOWED_ACTIONS`. Anything else is rejected.
- The compose file path comes from the DB row (which is seeded from
  `apps_config.APPS_CONFIG`), never from the request body.
- `subprocess.run` is invoked with a fixed argv list and `shell=False`, so no
  user-controlled string ever reaches a shell.
- Every action attempt is logged to `app_action_logs` with start/finish times,
  exit code, and tail of stdout/stderr.

Result classification:
- `success`        — command exited 0
- `not_running`    — logs requested but no containers are up
- `not_found`      — compose file missing on disk
- `not_applicable` — action not meaningful (e.g. pull on a build-only app)
- `not_configured` — docker CLI or socket not available in container
- `failed`         — non-zero exit, with structured message
- `timeout`        — command exceeded subprocess timeout
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import App, AppActionLog
from ..schemas import (
    AppActionLogResponse,
    AppActionRequest,
    AppActionResult,
    AppLogsResponse,
    AppResponse,
)

router = APIRouter(prefix="/api/apps", tags=["apps"])


ALLOWED_ACTIONS = ("start", "stop", "restart", "pull", "rebuild", "logs")

# Build argv templates. {compose_path} is substituted from the DB row, not from
# user input. No shell metacharacters are involved.
_COMPOSE_BIN = ("docker", "compose")

_ACTION_ARGS = {
    "start": ("up", "-d"),
    "stop": ("down",),
    "restart": ("restart",),
    "pull": ("pull",),
    "rebuild": ("up", "-d", "--build"),
    "logs": ("logs", "--tail", "200", "--no-color"),
}

# Subprocess timeout: kept here so timeout handling in execute_action can refer
# to a known value when constructing the user-facing message.
_SUBPROCESS_TIMEOUT_S = 300

_TAIL_BYTES = 4000  # ~4 KB of stdout/stderr persisted in the action log


def _check_docker_available() -> tuple[bool, str]:
    """Check if docker CLI and socket are available.

    Returns (available, reason) tuple.
    """
    import shutil
    from pathlib import Path

    # Check docker CLI
    if shutil.which("docker") is None:
        return False, "Docker CLI not found in PATH. Install docker-ce-cli in the container."

    # Check socket
    socket_path = Path("/var/run/docker.sock")
    if not socket_path.exists():
        return False, "Docker socket not found at /var/run/docker.sock. Mount the host socket."
    if not os.access(socket_path, os.R_OK | os.W_OK):
        return False, "Docker socket exists but is not readable/writable. Check permissions."

    return True, ""


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ComposeInfo:
    """Static analysis of a compose file used to drive UI affordances."""
    exists: bool
    has_image: bool
    has_build: bool

    @property
    def build_only(self) -> bool:
        return self.exists and self.has_build and not self.has_image


def _default_runner(argv: Sequence[str], cwd: str | None) -> RunResult:
    """Default subprocess runner. Overridable via `set_action_runner` for tests."""
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        shell=False,
        timeout=_SUBPROCESS_TIMEOUT_S,
    )
    return RunResult(
        exit_code=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


_runner: Callable[[Sequence[str], str | None], RunResult] = _default_runner


def set_action_runner(
    runner: Callable[[Sequence[str], str | None], RunResult],
) -> None:
    """Replace the subprocess runner. Used by tests to avoid touching docker."""
    global _runner
    _runner = runner


def reset_action_runner() -> None:
    global _runner
    _runner = _default_runner


def _get_app_or_404(db: Session, app_id: str) -> App:
    app = db.query(App).filter(App.app_id == app_id).first()
    if app is None:
        raise HTTPException(status_code=404, detail=f"App '{app_id}' not found")
    return app


def _build_argv(app: App, action: str) -> List[str]:
    if action not in _ACTION_ARGS:
        # Belt-and-suspenders: callers validate first, but never trust input.
        raise HTTPException(status_code=400, detail=f"Action '{action}' not allowed")
    return [
        *_COMPOSE_BIN,
        "-f",
        app.compose_path,
        "-p",
        app.compose_project,
        *_ACTION_ARGS[action],
    ]


def _tail(text: str, limit: int = _TAIL_BYTES) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[-limit:]


def _status_for_action(action: str, success: bool) -> str:
    if not success:
        return "unknown"
    if action in ("start", "restart", "rebuild", "pull"):
        return "running"
    if action == "stop":
        return "stopped"
    return "unknown"


def _inspect_compose(compose_path: str) -> ComposeInfo:
    """Scan a compose file for `image:` and `build:` so callers can decide
    whether `pull` is meaningful and surface missing-file states.

    Falls back to a tolerant text scan if PyYAML is unavailable or the file
    cannot be parsed: docker-compose files are diverse and we never want this
    helper to raise.
    """
    p = Path(compose_path)
    if not p.is_file():
        return ComposeInfo(exists=False, has_image=False, has_build=False)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        # File present but unreadable — treat as missing for UI purposes.
        return ComposeInfo(exists=False, has_image=False, has_build=False)

    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        return _inspect_compose_text(text)

    if not isinstance(data, dict):
        return _inspect_compose_text(text)
    services = data.get("services")
    if not isinstance(services, dict):
        return ComposeInfo(exists=True, has_image=False, has_build=False)

    has_image = False
    has_build = False
    for svc in services.values():
        if isinstance(svc, dict):
            if "image" in svc:
                has_image = True
            if "build" in svc:
                has_build = True
    return ComposeInfo(exists=True, has_image=has_image, has_build=has_build)


def _inspect_compose_text(text: str) -> ComposeInfo:
    """Best-effort fallback: scan compose file text for top-of-line `image:`
    and `build:` keys. Imperfect but resilient enough for the UI hint.
    """
    has_image = False
    has_build = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        stripped = line.lstrip()
        # Only consider mapping keys (end with `:` or `: <value>`).
        if stripped.startswith("image:") or stripped == "image:":
            has_image = True
        elif stripped.startswith("build:") or stripped == "build:":
            has_build = True
    return ComposeInfo(exists=True, has_image=has_image, has_build=has_build)


def _serialize_app(app: App) -> AppResponse:
    """Build an AppResponse and decorate it with derived compose-file info."""
    info = _inspect_compose(app.compose_path)
    response = AppResponse.model_validate(app)
    response.compose_exists = info.exists
    response.build_only = info.build_only if info.exists else None

    # Derive web endpoint from compose file ports
    web_url, web_port, can_open, reason = _extract_web_endpoint(app.compose_path)
    response.web_url = web_url
    response.web_port = web_port
    response.can_open = can_open
    response.open_unavailable_reason = reason

    return response


def _extract_web_endpoint(compose_path: str) -> tuple[str | None, int | None, bool, str | None]:
    """Extract single web endpoint from compose file.

    Returns (url, port, can_open, reason) tuple.
    Only returns can_open=True when exactly one web app port is detected.
    Internal/DB ports (5432, 3306, 6379, etc.) are excluded.
    """
    from pathlib import Path

    INTERNAL_PORTS = {5432, 3306, 6379, 27017, 9200, 9300, 8080, 8443}  # DB/internal ports
    p = Path(compose_path)
    if not p.is_file():
        return (None, None, False, "Compose file not found")

    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return (None, None, False, "Cannot read compose file")

    web_ports = []
    try:
        import yaml
        data = yaml.safe_load(text)
        if isinstance(data, dict):
            services = data.get("services", {})
            if isinstance(services, dict):
                for svc in services.values():
                    if isinstance(svc, dict):
                        ports = svc.get("ports", [])
                        if isinstance(ports, list):
                            for port_spec in ports:
                                port = _parse_port(port_spec)
                                if port and port not in INTERNAL_PORTS:
                                    web_ports.append(port)
    except Exception:  # noqa: BLE001
        # Fallback to text scan
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("-") and ":" in line:
                # Port mapping like "- 8080:80" or "- 3000:3000"
                port = _parse_port(line.strip('"').strip("'"))
                if port and port not in INTERNAL_PORTS:
                    web_ports.append(port)

    if len(web_ports) == 0:
        return (None, None, False, "No web port configured")
    elif len(web_ports) > 1:
        return (None, None, False, "Multiple web ports detected")
    else:
        port = web_ports[0]
        url = f"http://localhost:{port}"
        return (url, port, True, None)


def _parse_port(port_spec) -> int | None:
    """Parse docker port spec to extract host port.

    Handles: 8080, "8080", "8080:80", "127.0.0.1:8080:80", {"target": 80, "published": 8080}
    """
    if isinstance(port_spec, int):
        return port_spec
    if isinstance(port_spec, dict):
        return port_spec.get("published")
    if isinstance(port_spec, str):
        # Remove quotes
        port_spec = port_spec.strip('"').strip("'")
        # Handle "127.0.0.1:8080:80" or "8080:80" or "8080"
        parts = port_spec.split(":")
        if len(parts) >= 2:
            # Host port is second-to-last (last is container port)
            try:
                return int(parts[-2])
            except ValueError:
                return None
        elif len(parts) == 1:
            try:
                return int(parts[0])
            except ValueError:
                return None
    return None


def _serialize_log(log: AppActionLog, message: str | None) -> AppActionLogResponse:
    response = AppActionLogResponse.model_validate(log)
    response.message = message
    return response


def _get_latest_action(db: Session, app_id: int) -> AppActionLog | None:
    """Get the most recent action log for an app."""
    return (
        db.query(AppActionLog)
        .filter(AppActionLog.app_id == app_id)
        .order_by(AppActionLog.id.desc())
        .first()
    )


@router.get("/{app_id}/actions/latest", response_model=AppActionLogResponse)
def get_latest_action(app_id: str, db: Session = Depends(get_db)) -> AppActionLogResponse:
    """Get the latest action status for an app.

    Used by frontend to poll for action progress during long-running operations.
    Returns the most recent action log with phase and elapsed_seconds.
    """
    app = _get_app_or_404(db, app_id)
    log = _get_latest_action(db, app.id)
    if log is None:
        raise HTTPException(status_code=404, detail="No actions recorded for this app")

    # Calculate elapsed seconds if still running
    elapsed = None
    if log.finished_at is None and log.started_at:
        elapsed = int((datetime.utcnow() - log.started_at).total_seconds())
    elif log.finished_at and log.started_at:
        elapsed = int((log.finished_at - log.started_at).total_seconds())

    message = _summarize_failure(log.action, RunResult(log.exit_code or 0, log.stdout_tail or "", log.stderr_tail or "")) if log.result == "failed" else None
    if log.result == "success":
        message = f"docker compose {log.action} completed."
    elif log.result == "not_configured":
        message = f"Docker control not configured. See docs/DOCKER.md."
    elif log.result == "timeout":
        message = f"docker compose {log.action} timed out."

    response = AppActionLogResponse.model_validate(log)
    response.message = message
    if elapsed is not None:
        response.elapsed_seconds = elapsed

    return response


def _ps_quiet(app: App) -> RunResult:
    """Run `docker compose ps -q` to check whether any containers exist for
    this project. Returns the raw RunResult so callers can decide.
    """
    argv = [
        *_COMPOSE_BIN,
        "-f",
        app.compose_path,
        "-p",
        app.compose_project,
        "ps",
        "-q",
    ]
    cwd = str(Path(app.compose_path).parent)
    return _runner(argv, cwd)


@router.get("", response_model=List[AppResponse])
def list_apps(db: Session = Depends(get_db)) -> List[AppResponse]:
    rows = db.query(App).order_by(App.app_id.asc()).all()
    return [_serialize_app(row) for row in rows]


@router.get("/{app_id}", response_model=AppResponse)
def get_app(app_id: str, db: Session = Depends(get_db)) -> AppResponse:
    app = _get_app_or_404(db, app_id)
    return _serialize_app(app)


@router.get("/{app_id}/logs", response_model=AppLogsResponse)
def get_app_logs(
    app_id: str,
    tail: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> AppLogsResponse:
    app = _get_app_or_404(db, app_id)

    info = _inspect_compose(app.compose_path)
    if not info.exists:
        return AppLogsResponse(
            app_id=app.app_id,
            lines=[],
            result="not_found",
            message=(
                f"Compose file not found at {app.compose_path}. "
                "Clone or check out the app repo to enable logs."
            ),
        )

    # Cheap probe: if there are no project containers, there are no logs to
    # read. Skipping the actual `logs` call avoids docker emitting an error
    # for an uninitialized project.
    try:
        ps = _ps_quiet(app)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to read logs: {exc}"
        ) from exc

    if ps.exit_code == 0 and not ps.stdout.strip():
        return AppLogsResponse(
            app_id=app.app_id,
            lines=[],
            result="not_running",
            message="App is not running. Start it to produce logs.",
        )

    argv = [
        *_COMPOSE_BIN,
        "-f",
        app.compose_path,
        "-p",
        app.compose_project,
        "logs",
        "--tail",
        str(tail),
        "--no-color",
    ]
    cwd = str(Path(app.compose_path).parent)
    try:
        result = _runner(argv, cwd)
    except subprocess.TimeoutExpired as exc:
        return AppLogsResponse(
            app_id=app.app_id,
            lines=[],
            result="timeout",
            message=f"docker compose logs timed out after {exc.timeout}s.",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to read logs: {exc}"
        ) from exc

    if result.exit_code != 0:
        return AppLogsResponse(
            app_id=app.app_id,
            lines=[],
            result="failed",
            message=_summarize_failure("logs", result),
        )

    text = result.stdout or result.stderr or ""
    lines = text.splitlines()[-tail:]
    if not lines:
        return AppLogsResponse(
            app_id=app.app_id,
            lines=[],
            result="not_running",
            message="No log output yet.",
        )
    return AppLogsResponse(app_id=app.app_id, lines=lines, result="success")


@router.get("/{app_id}/action-logs", response_model=List[AppActionLogResponse])
def list_action_logs(
    app_id: str,
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> List[AppActionLogResponse]:
    app = _get_app_or_404(db, app_id)
    rows = (
        db.query(AppActionLog)
        .filter(AppActionLog.app_id == app.id)
        .order_by(AppActionLog.id.desc())
        .limit(limit)
        .all()
    )
    return [_serialize_log(row, message=None) for row in rows]


def _summarize_failure(action: str, run: RunResult) -> str:
    """Build a concise, UI-ready error sentence from a non-zero compose run.

    The full stderr/stdout tail is still saved on the action log for operator
    inspection; this is just the headline.
    """
    stderr = (run.stderr or "").strip()
    if stderr:
        first_line = next(
            (
                ln.strip()
                for ln in stderr.splitlines()
                if ln.strip() and not ln.lower().startswith("warn")
            ),
            stderr.splitlines()[0].strip(),
        )
        return f"docker compose {action} failed (exit {run.exit_code}): {first_line}"
    return f"docker compose {action} failed with exit code {run.exit_code}."


def _classify_logs_action(app: App) -> tuple[str, str, RunResult | None]:
    """Implements the `logs` action via execute_action. Returns
    (result, message, run-or-None) so the caller can finalize the log row.
    """
    try:
        ps = _ps_quiet(app)
    except subprocess.TimeoutExpired as exc:
        return (
            "timeout",
            f"docker compose ps timed out after {exc.timeout}s.",
            None,
        )
    except Exception as exc:  # noqa: BLE001
        return ("failed", f"Failed to probe containers: {exc}", None)

    if ps.exit_code == 0 and not ps.stdout.strip():
        return (
            "not_running",
            "App is not running. Start it to produce logs.",
            ps,
        )
    return ("success", "Containers detected. Use the logs panel for output.", ps)


@router.post(
    "/{app_id}/action",
    response_model=AppActionResult,
    status_code=status.HTTP_200_OK,
)
def execute_action(
    app_id: str,
    payload: AppActionRequest,
    db: Session = Depends(get_db),
) -> AppActionResult:
    action = payload.action
    if action not in ALLOWED_ACTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Action '{action}' not allowed. Allowed: "
                f"{', '.join(ALLOWED_ACTIONS)}"
            ),
        )

    app = _get_app_or_404(db, app_id)

    log = AppActionLog(
        app_id=app.id,
        action=action,
        result="pending",
        phase="queued",
        started_at=datetime.utcnow(),
    )
    db.add(log)
    db.flush()

    info = _inspect_compose(app.compose_path)
    success = False
    message: str | None = None

    if not info.exists:
        log.result = "not_found"
        log.exit_code = None
        message = (
            f"Compose file not found at {app.compose_path}. "
            "Check the app repo is present."
        )
        log.stderr_tail = _tail(message)
    elif action == "pull" and info.build_only:
        log.result = "not_applicable"
        log.exit_code = None
        message = (
            "This app is built locally (no `image:` declared); `pull` is not "
            "applicable. Use `rebuild` instead."
        )
        log.stderr_tail = _tail(message)
    elif action == "logs":
        result_kind, msg, ps = _classify_logs_action(app)
        log.result = result_kind
        message = msg
        if ps is not None:
            log.exit_code = ps.exit_code
            log.stdout_tail = _tail(ps.stdout)
            log.stderr_tail = _tail(ps.stderr)
        success = result_kind == "success"
    else:
        # Check docker availability before attempting action
        docker_ok, docker_reason = _check_docker_available()
        if not docker_ok:
            log.result = "not_configured"
            log.exit_code = None
            message = (
                f"Docker control not configured: {docker_reason} "
                "See docs/DOCKER.md for setup instructions."
            )
            log.stderr_tail = _tail(message)
        else:
            argv = _build_argv(app, action)
            cwd = str(Path(app.compose_path).parent)
            try:
                run = _runner(argv, cwd)
                success = run.exit_code == 0
                log.exit_code = run.exit_code
                log.stdout_tail = _tail(run.stdout)
                log.stderr_tail = _tail(run.stderr)
                if success:
                    log.result = "success"
                    message = f"docker compose {action} completed."
                else:
                    log.result = "failed"
                    message = _summarize_failure(action, run)
            except subprocess.TimeoutExpired as exc:
                log.result = "timeout"
                log.exit_code = None
                message = (
                    f"docker compose {action} timed out after {exc.timeout}s. "
                    "The command may still be running in the background."
                )
                log.stderr_tail = _tail(message)
            except Exception as exc:  # noqa: BLE001
                log.result = "failed"
                log.exit_code = -1
                message = f"Unexpected error running docker compose {action}: {exc}"
                log.stderr_tail = _tail(str(exc))

    log.finished_at = datetime.utcnow()

    app.last_action = action
    app.last_result = log.result
    app.last_updated = log.finished_at
    # Only mutate visible status when an action that should change it
    # actually succeeded; otherwise leave the prior status intact so the UI
    # doesn't flip to `unknown` on every benign no-op.
    if action != "logs" and log.result == "success":
        app.status = _status_for_action(action, True)

    db.commit()
    db.refresh(app)
    db.refresh(log)

    return AppActionResult(
        app=_serialize_app(app),
        log=_serialize_log(log, message=message),
    )
