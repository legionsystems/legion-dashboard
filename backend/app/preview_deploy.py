"""Preview deployment helpers (workflow slice 4).

Operators trigger ``deploy-preview`` to bring up a Work Item's PR branch on
the host's docker-compose stack, and ``revert-preview`` to roll back to the
project's known-good base branch. The router uses the orchestration helpers
in this module so the moving parts (git checkout, ``docker compose`` calls,
healthcheck) can be exercised — and stubbed — in isolation by tests.

The module is deliberately thin: it shells out to ``git`` and
``docker compose`` rather than wrapping them in heavier abstractions, so an
operator sees exactly what would happen at the terminal.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .models import WorkItem


# Branch the revert flow returns the worktree to. Slice 4 keeps this hard-
# coded to match the project's known-good base branch; slice 5+ may make it
# configurable per-app once additional repos come online.
REVERT_BASE_BRANCH = "feature/dashboard-bootstrap-control-plane"

# Docker compose service name used by the dashboard's compose project. The
# preview deploy rebuilds and restarts only this service.
PREVIEW_COMPOSE_SERVICE = "app"


# ---------------------------------------------------------------------------
# Effective-state guards
# ---------------------------------------------------------------------------


# Effective states from which ``deploy-preview`` is allowed. Anything outside
# this set is a 409 — we refuse to redeploy a merged item, blow away a
# rejected one, or step on an actively-building branch.
_DEPLOY_PREVIEW_SOURCE_STATES = frozenset(
    {
        "in_review",
        "preview_pending",
        "preview_ready",
        "code_reviewed",
        "changes_requested",
        "review_failed",
        "needs_rework",
    }
)


# Effective states that flatly disallow preview operations. Surfaces a clear
# 409 reason in the response so the UI can hide / explain.
_BLOCKED_PREVIEW_STATES = frozenset(
    {"merged", "implemented", "archived", "rejected", "certified", "ready_to_merge"}
)


def is_deploy_state_allowed(effective_state: Optional[str]) -> bool:
    """Return True when ``deploy-preview`` is allowed from this state."""
    if not effective_state:
        return False
    return effective_state in _DEPLOY_PREVIEW_SOURCE_STATES


def is_preview_state_blocked(effective_state: Optional[str]) -> bool:
    """Return True when this effective state is a hard preview block."""
    if not effective_state:
        return False
    return effective_state in _BLOCKED_PREVIEW_STATES


# ---------------------------------------------------------------------------
# Repo / compose resolution
# ---------------------------------------------------------------------------


def resolve_preview_repo(work_item: WorkItem) -> Tuple[str, str]:
    """Return ``(repo_path, repo_name)`` for the preview deploy.

    Mirrors ``app.routers.builder._resolve_target_repo`` so the gate and the
    deploy resolve to the same repo. The compose project lives in the repo
    root, so we use the repo path as the compose working directory too.
    """
    if work_item.target_app and "hub" in work_item.target_app.lower():
        return ("/srv/repo/lgn-hub", "lgn-hub")
    return ("/srv/repo/legion-dashboard", "legion-dashboard")


# ---------------------------------------------------------------------------
# Shell-out helpers
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    """Captured outcome of a subprocess invocation."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _run(cmd: List[str], cwd: str, timeout: int) -> CommandResult:
    """Run ``cmd`` in ``cwd`` and return a :class:`CommandResult`.

    A ``subprocess.TimeoutExpired`` is surfaced as a non-zero return code
    with the timeout note in ``stderr`` so callers can treat timeouts the
    same way they treat any other failure (non-zero -> abort).
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return CommandResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            returncode=124,
            stdout=(exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")),
            stderr=f"timed out after {timeout}s",
        )
    except FileNotFoundError as exc:
        return CommandResult(returncode=127, stdout="", stderr=str(exc))


def checkout_branch(repo_path: str, branch: str, timeout: int = 30) -> CommandResult:
    """Check out ``branch`` in ``repo_path``."""
    return _run(["git", "checkout", branch], cwd=repo_path, timeout=timeout)


def compose_build_and_up(
    repo_path: str,
    service: str = PREVIEW_COMPOSE_SERVICE,
    timeout: int = 600,
) -> CommandResult:
    """Run ``docker compose build <service> && docker compose up -d <service>``.

    Returns the first non-zero CommandResult, or the ``up -d`` result on
    success. Callers treat a non-zero return as an abort signal.
    """
    build = _run(
        ["docker", "compose", "build", service], cwd=repo_path, timeout=timeout
    )
    if not build.ok:
        return build
    return _run(
        ["docker", "compose", "up", "-d", service],
        cwd=repo_path,
        timeout=timeout,
    )


def run_healthcheck(
    repo_path: str,
    service: str = PREVIEW_COMPOSE_SERVICE,
    timeout: int = 30,
) -> CommandResult:
    """Verify the preview service is up.

    ``docker compose ps`` is enough for the operator's purposes: a healthy
    service yields a ``running`` row. We don't probe an in-container HTTP
    endpoint here because the dashboard's own startup health is observable
    via the existing apps page — duplicating it would couple this module to
    a hostname/port mapping the operator may not have configured yet.
    """
    return _run(
        ["docker", "compose", "ps", "--format", "json", service],
        cwd=repo_path,
        timeout=timeout,
    )


def parse_healthcheck(result: CommandResult) -> bool:
    """Return True when ``ps --format json`` shows a running container."""
    if not result.ok:
        return False
    import json

    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            # ``docker compose ps --format json`` may emit a JSON array.
            try:
                arr = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(arr, list):
                for item in arr:
                    if _entry_is_running(item):
                        return True
            continue
        if _entry_is_running(entry):
            return True
    return False


def _entry_is_running(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    state = (
        entry.get("State")
        or entry.get("state")
        or entry.get("status")
        or ""
    )
    return str(state).lower() == "running"


