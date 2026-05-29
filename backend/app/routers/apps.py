"""Apps operations router.

Safety contract:
- The action name MUST be one of `ALLOWED_ACTIONS`. Anything else is rejected.
- The compose file path comes from the DB row (which is seeded from
  `apps_config.APPS_CONFIG`), never from the request body.
- `subprocess.run` is invoked with a fixed argv list and `shell=False`, so no
  user-controlled string ever reaches a shell.
- Every action attempt is logged to `app_action_logs` with start/finish times,
  exit code, and tail of stdout/stderr.
"""
from __future__ import annotations

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

_TAIL_BYTES = 4000  # ~4 KB of stdout/stderr persisted in the action log


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


def _default_runner(argv: Sequence[str], cwd: str | None) -> RunResult:
    """Default subprocess runner. Overridable via `set_action_runner` for tests."""
    completed = subprocess.run(
        list(argv),
        cwd=cwd,
        capture_output=True,
        text=True,
        shell=False,
        timeout=300,
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


@router.get("", response_model=List[AppResponse])
def list_apps(db: Session = Depends(get_db)) -> List[App]:
    return db.query(App).order_by(App.app_id.asc()).all()


@router.get("/{app_id}", response_model=AppResponse)
def get_app(app_id: str, db: Session = Depends(get_db)) -> App:
    return _get_app_or_404(db, app_id)


@router.get("/{app_id}/logs", response_model=AppLogsResponse)
def get_app_logs(
    app_id: str,
    tail: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> AppLogsResponse:
    app = _get_app_or_404(db, app_id)
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
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"Failed to read logs: {exc}"
        ) from exc
    text = result.stdout or result.stderr or ""
    lines = text.splitlines()[-tail:]
    return AppLogsResponse(app_id=app.app_id, lines=lines)


@router.get("/{app_id}/action-logs", response_model=List[AppActionLogResponse])
def list_action_logs(
    app_id: str,
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> List[AppActionLog]:
    app = _get_app_or_404(db, app_id)
    return (
        db.query(AppActionLog)
        .filter(AppActionLog.app_id == app.id)
        .order_by(AppActionLog.id.desc())
        .limit(limit)
        .all()
    )


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

    argv = _build_argv(app, action)
    cwd = str(Path(app.compose_path).parent)

    log = AppActionLog(
        app_id=app.id,
        action=action,
        result="pending",
        started_at=datetime.utcnow(),
    )
    db.add(log)
    db.flush()

    try:
        run = _runner(argv, cwd)
        success = run.exit_code == 0
        log.exit_code = run.exit_code
        log.stdout_tail = _tail(run.stdout)
        log.stderr_tail = _tail(run.stderr)
        log.result = "success" if success else "failed"
    except Exception as exc:  # noqa: BLE001
        success = False
        log.exit_code = -1
        log.stderr_tail = _tail(str(exc))
        log.result = "failed"

    log.finished_at = datetime.utcnow()

    app.last_action = action
    app.last_result = log.result
    app.last_updated = log.finished_at
    if action != "logs":
        app.status = _status_for_action(action, success)

    db.commit()
    db.refresh(app)
    db.refresh(log)

    return AppActionResult(
        app=AppResponse.model_validate(app),
        log=AppActionLogResponse.model_validate(log),
    )
