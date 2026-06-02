"""Preview deployment helpers (workflow slices 4 + 4b).

Operators trigger ``deploy-preview`` to bring up a Work Item's PR branch on
the host's docker-compose stack, and ``revert-preview`` to roll back to the
project's known-good base branch. Slice 4b moved the side-effectful work
(git checkout, ``docker compose`` build/up, healthcheck) out of the
dashboard container — which mounts ``/srv/repo`` read-only and cannot run
``docker compose`` against the host daemon for arbitrary branches — and
into a host-side executor reachable at
``http://host.docker.internal:8766/preview``.

This module owns the dashboard side of that contract: the effective-state
guards, the repo-to-compose resolution, and ``call_host_executor`` which
serializes the request, sends it, and parses the response. The router
in ``app.routers.work_items`` uses these helpers so the host-executor
boundary is testable in isolation.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional, Tuple

from .models import WorkItem


# Branch the revert flow returns the worktree to. Slice 4 keeps this hard-
# coded to match the project's known-good base branch; slice 5+ may make it
# configurable per-app once additional repos come online.
REVERT_BASE_BRANCH = "feature/dashboard-bootstrap-control-plane"

# Docker compose service name used by the dashboard's compose project. The
# preview deploy rebuilds and restarts only this service.
PREVIEW_COMPOSE_SERVICE = "app"

# Default host-executor URL — overrideable via env so tests / staged
# rollouts can point at a stub. The default targets the host loopback via
# the ``host.docker.internal`` alias the compose file already configures.
DEFAULT_HOST_EXECUTOR_URL = "http://host.docker.internal:8766/preview"

# Default executor timeout. The build + up + healthcheck can take several
# minutes on a cold image cache; the dashboard waits the executor out
# rather than racing it with a shorter HTTP timeout.
DEFAULT_EXECUTOR_TIMEOUT_SECONDS = 1500


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


# Effective states from which ``merge`` is allowed (slice 5). Only items
# the operator has actively cleared for merge can be merged.
_MERGE_SOURCE_STATES = frozenset({"ready_to_merge", "certified", "blocked_merge"})


def is_merge_state_allowed(effective_state: Optional[str]) -> bool:
    """Return True when ``merge`` is allowed from this state."""
    if not effective_state:
        return False
    return effective_state in _MERGE_SOURCE_STATES


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
# Host executor client
# ---------------------------------------------------------------------------


@dataclass
class ExecutorResponse:
    """Parsed response from the host-side preview executor.

    ``success`` is the single boolean the router gates metadata writes on:
    if False, the router must release the lock with a failed status and
    must not stamp ``preview_deployed = True``. ``error_code`` is a stable
    short identifier; ``error`` is the human-readable message. ``raw``
    preserves the original payload for tests / debug logging.
    ``merge_commit_sha`` is populated only by the ``merge_pr`` action.
    """

    success: bool
    commit_sha: Optional[str] = None
    health_status: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    raw: Optional[dict] = None
    merge_commit_sha: Optional[str] = None


def _executor_url() -> str:
    return os.environ.get("LEGION_PREVIEW_EXECUTOR_URL", DEFAULT_HOST_EXECUTOR_URL)


def _executor_api_key() -> str:
    """Return the executor API key from the dashboard's environment.

    The dashboard and executor share ``API_SERVER_KEY``. Returning an empty
    string when unset lets the executor return its own 401 rather than the
    dashboard crashing on a missing env var — surfaces the misconfig the
    same way as a stale key.
    """
    return os.environ.get("API_SERVER_KEY", "")


def call_host_executor(
    action: str,
    repo_path: str,
    branch: str,
    service: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: int = DEFAULT_EXECUTOR_TIMEOUT_SECONDS,
    url: Optional[str] = None,
    pr_number: Optional[int] = None,
    base_branch: Optional[str] = None,
) -> ExecutorResponse:
    """POST a preview action to the host executor and parse the response.

    Returns an :class:`ExecutorResponse` in every case — network errors,
    non-2xx HTTP responses, and malformed JSON all yield ``success=False``
    with a descriptive ``error`` / ``error_code``. The router translates
    that into a 5xx without leaking secrets.

    The API key falls back to ``API_SERVER_KEY`` from the environment so
    production code does not need to pass it explicitly; tests override it
    via the ``api_key`` argument or by monkeypatching this whole function.
    """
    request_payload = {
        "action": action,
        "repo_path": repo_path,
        "branch": branch,
    }
    if service is not None:
        request_payload["service"] = service
    if pr_number is not None:
        request_payload["pr_number"] = pr_number
    if base_branch is not None:
        request_payload["base_branch"] = base_branch

    body = json.dumps(request_payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key if api_key is not None else _executor_api_key(),
    }
    target = url or _executor_url()
    req = urllib.request.Request(target, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_text = resp.read().decode("utf-8")
            status_code = resp.status
    except urllib.error.HTTPError as exc:
        # Executor returned a non-2xx with (hopefully) a JSON body. Try to
        # surface the structured error; fall back to the HTTP status.
        # For merge_pr, the executor may include merge_commit_sha even on
        # failure (e.g., merge succeeded but post-merge deploy failed), so
        # we must preserve it to distinguish blocked_merge from
        # merged_deployment_failed.
        try:
            err_body = exc.read().decode("utf-8") if exc.fp is not None else ""
            data = json.loads(err_body) if err_body else {}
        except (ValueError, AttributeError):
            data = {}
        return ExecutorResponse(
            success=False,
            error=str(data.get("error") or f"executor HTTP {exc.code}"),
            error_code=str(data.get("error_code") or "executor_http_error"),
            raw=data if isinstance(data, dict) else None,
            merge_commit_sha=data.get("merge_commit_sha") or None,
        )
    except urllib.error.URLError as exc:
        return ExecutorResponse(
            success=False,
            error=f"executor unreachable: {exc.reason}",
            error_code="executor_unreachable",
        )
    except (TimeoutError, OSError) as exc:
        return ExecutorResponse(
            success=False,
            error=f"executor request failed: {exc}",
            error_code="executor_unreachable",
        )

    try:
        data = json.loads(raw_text)
    except ValueError:
        return ExecutorResponse(
            success=False,
            error="executor returned non-JSON response",
            error_code="executor_bad_response",
            raw={"status": status_code, "body": raw_text[:200]},
        )
    if not isinstance(data, dict):
        return ExecutorResponse(
            success=False,
            error="executor returned non-object JSON",
            error_code="executor_bad_response",
            raw={"status": status_code},
        )

    return ExecutorResponse(
        success=bool(data.get("success")),
        commit_sha=data.get("commit_sha") or None,
        health_status=data.get("health_status") or None,
        error=data.get("error") or None,
        error_code=data.get("error_code") or None,
        raw=data,
        merge_commit_sha=data.get("merge_commit_sha") or None,
    )
