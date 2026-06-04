"""Dashboard-side orchestration for the per-task worktree.

This module is the thin glue between :mod:`app.worktree_paths` (pure
helpers) and the in-image tool ``legion-worktree-create`` (which
runs the actual ``git worktree add`` inside the dashboard
container).

The dashboard container bind-mounts ``/srv/repo`` read-write so the
container can write the per-worktree metadata that ``git worktree
add`` deposits in
``<shared_repo>/.git/worktrees/<name>/``. A read-only mount blocks
that write, and ``Start Build`` fails before the orchestrator has
a chance to surface the underlying error. The mount is rw on the
app service only — every other service still mounts ``/srv/repo``
read-only because they never run ``git worktree add``.

The tool location is resolved at import time from the
``LEGION_WORKTREE_CREATE_TOOL`` environment variable; if unset, it
falls back to :data:`DEFAULT_WORKTREE_CREATE_TOOL`
(``/usr/local/bin/legion-worktree-create``), which is the path the
Dockerfile installs the repo-owned script to. This lets the
dashboard be deployed without depending on any host-only path
under the operator's home directory.

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
from typing import List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from .worktree_paths import (
    build_task_worktree_path,
    is_shared_repo_path,
)


DEFAULT_WORKTREE_CREATE_TOOL = "/usr/local/bin/legion-worktree-create"
WORKTREE_CREATE_TOOL = os.environ.get(
    "LEGION_WORKTREE_CREATE_TOOL", DEFAULT_WORKTREE_CREATE_TOOL
)


# Per-repo integration base refs. The dashboard-side worktree
# creator starts a new feature branch from one of these so the
# generated worktree is never off-base. Each repo can declare its
# own list in priority order; the first ref that resolves on the
# host is used (the host tool performs the existence check). This
# prevents a hub-targeted work item from being asked to start from
# a dashboard-only branch.
_REPO_BASE_REFS = {
    "legion-dashboard": (
        "feature/dashboard-bootstrap-control-plane",
        "main",
    ),
    "lgn-hub": (
        "feature/dashboard-bootstrap-control-plane",
        "main",
    ),
}


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


def _short_id(value: Optional[int], width: int = 6) -> str:
    """Render a stable short identifier (zero-padded) for ``value``."""
    if value is None or value < 0:
        return "x" * width
    return str(value).rjust(width, "0")


def _feature_branch_for_work_item(
    work_item,
    builder_task_id: Optional[int] = None,
) -> str:
    """Return the feature branch name the per-task worktree uses.

    Format: ``feature/wi-<id>-<safe-slug>-t_<builder_task_id>`` (or
    ``feature/<safe-slug>`` when no work item id is available).
    The ``-t_<builder_task_id>`` suffix is what makes a retry or
    resend produce a fresh branch name — ``git worktree add``
    cannot check out the same branch in two worktrees, so the
    suffix has to be unique per attempt. We use the same
    zero-padded short-id format as the worktree path so the
    branch suffix and path suffix read identically.

    Stable per (work_item, builder_task_id) tuple and short enough
    to stay within git's 63-char refname limit when combined with
    ``refs/heads/`` and the worktree folder.
    """
    wi_id = getattr(work_item, "id", None)
    title = getattr(work_item, "title", None) or "task"
    slug = re.sub(r"[^a-z0-9_\-]+", "-", title.strip().lower())
    slug = re.sub(r"[-_]+", "-", slug).strip("-_")
    if len(slug) > 24:
        slug = slug[:24].rstrip("-_")
    if not slug:
        slug = "task"
    if wi_id is None:
        return f"feature/{slug}"
    if builder_task_id is None:
        return f"feature/wi-{wi_id}-{slug}"
    return f"feature/wi-{wi_id}-{slug}-t_{_short_id(builder_task_id)}"


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


def _candidate_base_refs(worktree_path: str) -> Tuple[str, ...]:
    """Return the ordered list of base refs the orchestrator should
    try for the repo slug embedded in ``worktree_path``.

    The list is repo-specific: a hub-targeted work item MUST NOT
    fall back to the dashboard's base refs. Repos that do not
    declare a list fall back to ``("main",)`` so a fresh repo
    can still create a worktree.
    """
    repo_slug = _shared_repo_slug_for_worktree(worktree_path)
    refs = _REPO_BASE_REFS.get(repo_slug, ("main",))
    if not refs:
        return ("main",)
    return tuple(refs)


def _attempt_worktree_create_with_fallback(
    worktree_path: str,
    feature_branch: str,
    candidate_refs: Sequence[str],
    timeout: int = 60,
) -> Tuple[str, dict]:
    """Try the host tool against each candidate base ref in order.

    Returns ``(chosen_base_ref, result_dict)`` on the first
    successful invocation. Raises :class:`RuntimeError` if every
    candidate ref fails, with a clear aggregated error message
    listing each attempted ref and the tool's stderr so the
    operator can see exactly what went wrong.

    The host tool itself only accepts a single ``--base-ref``; it
    does not loop. This wrapper provides the loop so the
    operator-visible behaviour is "try each ref in order, stop
    on the first that resolves".
    """
    if not candidate_refs:
        raise RuntimeError(
            f"worktree-create failed for {worktree_path!r}: "
            f"no candidate base refs configured"
        )
    attempts: List[Tuple[str, str, str]] = []  # (ref, returncode, stderr)
    for ref in candidate_refs:
        ok, parsed, stderr = _worktree_create_result(
            worktree_path=worktree_path,
            feature_branch=feature_branch,
            base_ref=ref,
            timeout=timeout,
        )
        if ok:
            return ref, parsed
        attempts.append((ref, "non-zero", stderr or "tool reported failure"))
    # All candidates failed. Surface every attempt so the operator
    # can see what was tried.
    attempt_lines = "\n".join(
        f"  - base ref {ref!r}: {reason} ({stderr.strip()[:200] or 'no stderr'})"
        for ref, reason, stderr in attempts
    )
    raise RuntimeError(
        f"worktree-create failed for {worktree_path!r} "
        f"(branch {feature_branch!r}); tried {len(attempts)} base "
        f"ref(s), none resolved:\n{attempt_lines}"
    )


def ensure_task_worktree(
    db: Session,  # noqa: ARG001 - placeholder for future audit logging
    work_item,
    builder_task_id: Optional[int] = None,
    *,
    base_ref: Optional[str] = None,
) -> Tuple[str, str, str, dict]:
    """Create or reuse the per-task worktree for ``work_item``.

    Returns a tuple of
    ``(worktree_path, feature_branch, chosen_base_ref, result_dict)``.
    The worktree path matches the deterministic helper in
    :mod:`app.worktree_paths`. ``feature_branch`` is the branch
    the builder should commit to. ``chosen_base_ref`` is the
    ref the host tool actually used (one of the candidate refs
    tried in order; the first that resolved). ``result_dict``
    is the raw JSON the host tool returned; callers can stash
    it for audit logging.

    Raises :class:`RuntimeError` if the worktree cannot be
    created or reused. The router translates the error into a
    409 with a clear ``blocker_code``.
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

    feature_branch = _feature_branch_for_work_item(
        work_item, builder_task_id=builder_task_id
    )
    if base_ref is not None:
        # Caller-supplied ref: try it once, no fallback.
        ok, parsed, stderr = _worktree_create_result(
            worktree_path=worktree_path,
            feature_branch=feature_branch,
            base_ref=base_ref,
        )
        if not ok:
            raise RuntimeError(
                f"worktree-create failed for {worktree_path!r} "
                f"(branch {feature_branch!r}, base {base_ref!r}): "
                f"{stderr or parsed}"
            )
        return worktree_path, feature_branch, base_ref, parsed
    # Repo-specific candidate list with fallbacks.
    candidate_refs = _candidate_base_refs(worktree_path)
    chosen_base_ref, parsed = _attempt_worktree_create_with_fallback(
        worktree_path=worktree_path,
        feature_branch=feature_branch,
        candidate_refs=candidate_refs,
    )
    return worktree_path, feature_branch, chosen_base_ref, parsed
