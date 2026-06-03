"""Dashboard-side orchestration for the per-task worktree.

This module is the thin glue between :mod:`app.worktree_paths` (pure
helpers) and the host-side tool
``/root/.hermes/LEGION_TOOLS/bin/legion-worktree-create`` (which
runs the actual ``git worktree add`` because the dashboard
container mounts ``/srv/repo`` read-only).

The router calls :func:`ensure_task_worktree` before generating the
implementation Kanban card prompt. If the worktree cannot be
created, the router returns a 409 with a clear error so the
operator can intervene. The worktree-creation side effect is
visible to the operator via the same JSON status response the
executor-style tools return.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from .worktree_paths import (
    build_task_worktree_path,
    is_shared_repo_path,
)


WORKTREE_CREATE_TOOL = "/root/.hermes/LEGION_TOOLS/bin/legion-worktree-create"


# Allowed integration base refs. The dashboard-side worktree creator
# always starts a new feature branch from one of these so the
# generated worktree is never off-base.
INTEGRATION_BASE_REFS = (
    "feature/dashboard-bootstrap-control-plane",
)


def _shared_repo_slug_for_worktree(worktree_path: str) -> str:
    """Return the shared-repo slug embedded in the worktree path.

    Examples
    --------
    >>> _shared_repo_slug_for_worktree(
    ...     "/srv/worktrees/legion-dashboard-wi-17-task-t_000017"
    ... )
    'legion-dashboard'
    >>> _shared_repo_slug_for_worktree(
    ...     "/srv/worktrees/lgn-hub-wi-7-task-t_000007"
    ... )
    'lgn-hub'
    """
    rel = worktree_path[len("/srv/worktrees/") :]
    head = rel.split("/", 1)[0]
    return head


def _feature_branch_for_work_item(work_item) -> str:
    """Return the feature branch name the per-task worktree uses.

    Format: ``feature/wi-<id>-<safe-slug>``. Stable per Work Item
    and short enough to stay within git's 63-char refname limit when
    combined with ``refs/heads/`` and the worktree folder.
    """
    wi_id = getattr(work_item, "id", None)
    title = getattr(work_item, "title", None) or "task"
    slug = re.sub(r"[^a-z0-9_\-]+", "-", title.strip().lower())
    slug = re.sub(r"[-_]+", "-", slug).strip("-_")
    if len(slug) > 32:
        slug = slug[:32].rstrip("-_")
    if not slug:
        slug = "task"
    if wi_id is None:
        return f"feature/{slug}"
    return f"feature/wi-{wi_id}-{slug}"


def _worktree_create_result(
    worktree_path: str,
    feature_branch: str,
    base_ref: str,
    timeout: int = 60,
) -> Tuple[bool, dict, str]:
    """Invoke the host-side ``legion-worktree-create`` tool.

    Returns ``(success, parsed_json, raw_stdout)``. ``success`` is
    True only if the tool exited 0 and the JSON body reports
    ``"success": True``. Failures include the stderr in the third
    tuple element for diagnostics.
    """
    if not os.path.isfile(WORKTREE_CREATE_TOOL):
        return False, {}, f"missing tool: {WORKTREE_CREATE_TOOL}"
    if not os.access(WORKTREE_CREATE_TOOL, os.X_OK):
        return False, {}, f"tool not executable: {WORKTREE_CREATE_TOOL}"
    try:
        proc = subprocess.run(
            [
                WORKTREE_CREATE_TOOL,
                "--worktree-path",
                worktree_path,
                "--feature-branch",
                feature_branch,
                "--base-ref",
                base_ref,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return False, {}, f"timeout after {exc.timeout}s"
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return False, {}, stderr or f"exit {proc.returncode}"
    try:
        parsed = json.loads(stdout) if stdout else {}
    except ValueError:
        return False, {}, f"non-JSON output: {stdout[:200]}"
    if not isinstance(parsed, dict) or not parsed.get("success"):
        return False, parsed, stderr or "tool reported failure"
    return True, parsed, stderr


def ensure_task_worktree(
    db: Session,  # noqa: ARG001 - placeholder for future audit logging
    work_item,
    builder_task_id: Optional[int] = None,
    *,
    base_ref: Optional[str] = None,
) -> Tuple[str, str, dict]:
    """Create or reuse the per-task worktree for ``work_item``.

    Returns a tuple of ``(worktree_path, feature_branch, result_dict)``.
    The worktree path matches the deterministic helper in
    :mod:`app.worktree_paths`. ``feature_branch`` is the branch the
    builder should commit to. ``result_dict`` is the raw JSON the
    host tool returned; callers can stash it for audit logging.

    Raises :class:`RuntimeError` if the worktree cannot be created
    or reused. The router translates the error into a 409 with a
    clear ``blocker_code``.
    """
    worktree_path = build_task_worktree_path(
        work_item, builder_task_id=builder_task_id
    )
    if is_shared_repo_path(worktree_path):
        raise RuntimeError(
            f"worktree path {worktree_path!r} collides with a shared "
            f"operator/control repo path; refusing to ensure task "
            f"worktree"
        )

    feature_branch = _feature_branch_for_work_item(work_item)
    chosen_base_ref = base_ref or INTEGRATION_BASE_REFS[0]
    ok, parsed, stderr = _worktree_create_result(
        worktree_path=worktree_path,
        feature_branch=feature_branch,
        base_ref=chosen_base_ref,
    )
    if not ok:
        raise RuntimeError(
            f"worktree-create failed for {worktree_path!r} "
            f"(branch {feature_branch!r}, base {chosen_base_ref!r}): "
            f"{stderr or parsed}"
        )
    return worktree_path, feature_branch, parsed
