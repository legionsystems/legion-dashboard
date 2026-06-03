"""Deterministic, safe task-worktree path generation for LEGION.

When a Work Item is sent to the Hermes Kanban builder, the builder
should run inside a dedicated task worktree under ``/srv/worktrees/``
rather than the shared ``/srv/repo/<repo>`` operator/control worktree.
This module is the single source of truth for:

* slugifying a work-item title into a safe filesystem component
* computing a deterministic task identifier from the work-item id
  and (optional) builder task id
* assembling the full worktree path under ``/srv/worktrees/``
* validating that a candidate path lives under ``/srv/worktrees/``
  and contains no unsafe characters

The helpers are pure functions so the prompt-body assembler, the
worktree-creation CLI, and the cleanup report can all use the same
identifiers without drift.
"""
from __future__ import annotations

import re
from typing import Optional


# Hard-coded rules. These are not user-configurable because they are
# invariants of the operational model.
SHARED_REPO_PATH = "/srv/repo/legion-dashboard"
SHARED_HUB_PATH = "/srv/repo/lgn-hub"
SHARED_REPO_PREFIXES = ("/srv/repo/legion-dashboard", "/srv/repo/lgn-hub")
WORKTREES_ROOT = "/srv/worktrees"

# Path-component safe alphabet. Anything outside this set is replaced
# with ``_``. We keep lowercase letters, digits, ``-`` and ``_``.
_SAFE_SLUG_RE = re.compile(r"[^a-z0-9_\-]+")

# A conservative length cap so the resulting path is reasonable to
# inspect, copy-paste, and embed in a Kanban card body.
MAX_SLUG_LEN = 48
MAX_PATH_LEN = 240


def _slugify(value: Optional[str]) -> str:
    """Return a safe filesystem slug for ``value`` (lowercase, ascii)."""
    if not value:
        return ""
    lowered = value.strip().lower()
    # Replace any character that is not in the safe alphabet with ``_``.
    slug = _SAFE_SLUG_RE.sub("-", lowered)
    # Collapse repeated separators and trim leading/trailing ones.
    slug = re.sub(r"[-_]+", "-", slug).strip("-_")
    if len(slug) > MAX_SLUG_LEN:
        slug = slug[:MAX_SLUG_LEN].rstrip("-_")
    return slug


def _short_id(value: Optional[int], width: int = 6) -> str:
    """Render a stable short identifier (zero-padded) for ``value``."""
    if value is None or value < 0:
        return "x" * width
    return str(value).rjust(width, "0")


def _worktree_id(work_item_id: Optional[int]) -> str:
    """Stable task identifier used in worktree paths.

    Format: ``t_<6-digit-work-item-id>``. ``t_`` prefix makes it
    visually obvious in a path listing that this is a task worktree.
    """
    return f"t_{_short_id(work_item_id)}"


def is_shared_repo_path(path: Optional[str]) -> bool:
    """Return True if ``path`` is the shared operator/control worktree.

    Used by the prompt assembler to fail fast when an implementation
    card is generated against the shared repo rather than a task
    worktree.
    """
    if not path:
        return False
    norm = path.rstrip("/")
    return any(norm == prefix or norm.startswith(prefix + "/") for prefix in SHARED_REPO_PREFIXES)


def is_under_worktrees(path: Optional[str]) -> bool:
    """Return True if ``path`` is under the dedicated worktrees root."""
    if not path:
        return False
    norm = path.rstrip("/")
    return norm == WORKTREES_ROOT or norm.startswith(WORKTREES_ROOT + "/")


def shared_repo_path_for_work_item(work_item) -> str:
    """Return the shared operator/control repo path for a work item.

    Mirrors the legacy ``_resolve_target_repo_path`` derivation in
    :mod:`app.routers.builder` so the failure path uses the same
    baseline.
    """
    if getattr(work_item, "target_app", None) and "hub" in work_item.target_app.lower():
        return SHARED_HUB_PATH
    return SHARED_REPO_PATH


def build_task_worktree_path(
    work_item,
    builder_task_id: Optional[int] = None,
    *,
    title_slug: Optional[str] = None,
    worktree_id: Optional[str] = None,
) -> str:
    """Compute a safe, deterministic task worktree path.

    Examples
    --------

    >>> build_task_worktree_path(WI(id=17, title="Auto-Assign Stance"),
    ...                          builder_task_id=42)
    '/srv/worktrees/legion-dashboard/wi-17-auto-assign-stance/t_000042'

    Format: ``/srv/worktrees/<repo-slug>/<work-item-tag>-<title-slug>/<task-id>``.

    The repo slug is its own path component so the host-side
    ``legion-worktree-create`` tool can derive the source shared
    operator/control repo (which lives at ``/srv/repo/<repo-slug>``)
    without parsing. The work item tag and title slug are kept
    readable in the path listing.

    ``work_item.id`` is always included. ``builder_task_id`` is
    included when known so re-sending a work item to the builder
    (which currently creates a new BuilderTask row) does not reuse
    a stale worktree folder associated with a previous attempt.
    """
    repo_slug = "legion-dashboard"
    if getattr(work_item, "target_app", None) and "hub" in work_item.target_app.lower():
        repo_slug = "lgn-hub"
    wi_id = getattr(work_item, "id", None)
    slug = _slugify(title_slug if title_slug is not None else getattr(work_item, "title", None))
    if not slug:
        slug = "task"
    wi_tag = f"wi-{wi_id}" if wi_id is not None else "wi"
    leaf = f"{wi_tag}-{slug}"
    if builder_task_id is not None:
        leaf_id = _worktree_id(builder_task_id)
    else:
        leaf_id = _worktree_id(wi_id)
    folder_parts = [repo_slug, leaf, leaf_id]
    folder = "/".join(folder_parts)
    if len(folder) > MAX_PATH_LEN - len(WORKTREES_ROOT) - 2:
        folder = folder[: MAX_PATH_LEN - len(WORKTREES_ROOT) - 2]
    path = f"{WORKTREES_ROOT}/{folder}"
    _validate_worktree_path(path)
    return path


def _validate_worktree_path(path: str) -> None:
    """Raise :class:`ValueError` if ``path`` is not a safe worktree path.

    This is a defense-in-depth check; ``build_task_worktree_path`` is
    the only intended caller, but any future entry point that wants
    to bypass the helper must still produce a path that passes this
    validator.
    """
    if not path:
        raise ValueError("worktree path is empty")
    norm = path.rstrip("/")
    if norm == WORKTREES_ROOT:
        raise ValueError("worktree path is the worktrees root itself")
    if not norm.startswith(WORKTREES_ROOT + "/"):
        raise ValueError(
            f"worktree path must live under {WORKTREES_ROOT}, got {path!r}"
        )
    if is_shared_repo_path(norm):
        raise ValueError(
            f"worktree path collides with a shared operator/control "
            f"repo path: {path!r}"
        )
    if ".." in norm.split("/"):
        raise ValueError(f"worktree path contains '..': {path!r}")
    # Every non-root component must be a non-empty slug from the
    # safe alphabet. ``path.split('/')`` would yield a leading empty
    # for absolute paths, so iterate the meaningful parts.
    parts = [p for p in norm.split("/") if p]
    for component in parts:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]*", component):
            raise ValueError(
                f"worktree path component {component!r} is not a safe "
                f"slug (lowercase letters, digits, '-' or '_' only)"
            )
