"""Repo safety gate + repo lock manager (workflow slice 3).

This module provides the gate that ``builder`` calls before starting a build
or rework run on a target repo. The gate fails the run when:

  * the repo has staged or unstaged modifications, or
  * the repo has untracked, non-ignored files, or
  * another active ``RepoLock`` already owns the repo.

On a successful gate, the caller acquires a lock that should be released when
the builder run completes (successfully, with failure, or aborted).

The module shells out to ``git`` rather than using a Python git library so
that the gate observes exactly what an operator would see at the shell.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from .models import RepoLock
from .schemas import RepoSafetyResult


# Files / patterns that must never block the safety gate, even when they show
# up as dirty in a developer worktree. Hermes writes ``.hermes-config.yaml``
# into the repo as part of its own bootstrap; treating it as a blocker would
# permanently jam the gate.
_IGNORED_DIRTY_FILES = frozenset({".hermes-config.yaml"})


@dataclass
class LockResult:
    """Outcome of ``acquire_repo_lock``.

    ``acquired`` is True only when a brand-new lock row was created. If an
    active lock already exists, ``acquired`` is False and ``existing_lock``
    holds the conflicting row.
    """

    acquired: bool
    lock: Optional[RepoLock] = None
    existing_lock: Optional[RepoLock] = None
    blocker_code: Optional[str] = None
    blocker_message: Optional[str] = None


def _run_git(repo_path: str, args: List[str]) -> subprocess.CompletedProcess:
    """Run ``git`` in ``repo_path`` and return the completed process.

    Uses ``-C`` so the caller's CWD is irrelevant. We deliberately do not
    raise on non-zero exits — callers inspect ``returncode`` themselves.
    """
    return subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


def _filter_ignored(files: List[str]) -> List[str]:
    """Drop files that the gate intentionally ignores (e.g. hermes config)."""
    out: List[str] = []
    for f in files:
        name = os.path.basename(f)
        if name in _IGNORED_DIRTY_FILES:
            continue
        out.append(f)
    return out


def check_repo_clean(repo_path: str) -> RepoSafetyResult:
    """Inspect ``repo_path`` and return whether it is safe to start a build.

    Runs the same commands an operator would run at the shell:

      * ``git status --short`` — overview / fallback parsing source.
      * ``git diff --quiet`` — exits 1 when unstaged changes exist.
      * ``git diff --cached --quiet`` — exits 1 when staged changes exist.
      * ``git ls-files --others --exclude-standard`` — non-ignored untracked.
      * ``git branch --show-current`` — current branch name.
      * ``git rev-parse HEAD`` — current commit SHA.

    Returns a fully-populated :class:`RepoSafetyResult`.
    """
    if not os.path.isdir(repo_path):
        return RepoSafetyResult(
            repo_path=repo_path,
            is_clean=False,
            blocker_code="repo_not_found",
            blocker_message=f"Repo path does not exist: {repo_path}",
        )

    # Current branch + commit (informational; failures here don't block the
    # gate on their own — we still record what we can).
    branch_proc = _run_git(repo_path, ["branch", "--show-current"])
    commit_proc = _run_git(repo_path, ["rev-parse", "HEAD"])
    current_branch = (
        branch_proc.stdout.strip() if branch_proc.returncode == 0 else None
    )
    current_commit = (
        commit_proc.stdout.strip() if commit_proc.returncode == 0 else None
    )

    # Unstaged changes — `git diff --quiet` exits 1 when changes exist.
    unstaged_proc = _run_git(repo_path, ["diff", "--quiet"])
    has_unstaged = unstaged_proc.returncode != 0

    # Staged changes — `git diff --cached --quiet` exits 1 when changes exist.
    staged_proc = _run_git(repo_path, ["diff", "--cached", "--quiet"])
    has_staged = staged_proc.returncode != 0

    # Untracked, non-ignored files.
    untracked_proc = _run_git(
        repo_path, ["ls-files", "--others", "--exclude-standard"]
    )
    untracked_raw: List[str] = []
    if untracked_proc.returncode == 0:
        untracked_raw = [
            line for line in untracked_proc.stdout.splitlines() if line.strip()
        ]
    untracked_files = _filter_ignored(untracked_raw)

    # Parse `git status --short` to produce dirty/staged file lists. Format:
    # ``XY path`` where X = index status, Y = worktree status.
    status_proc = _run_git(repo_path, ["status", "--short"])
    dirty_files: List[str] = []
    staged_files: List[str] = []
    if status_proc.returncode == 0:
        for line in status_proc.stdout.splitlines():
            if len(line) < 4:
                continue
            index_status = line[0]
            worktree_status = line[1]
            path = line[3:].strip()
            name = os.path.basename(path)
            if name in _IGNORED_DIRTY_FILES:
                continue
            if index_status not in (" ", "?"):
                staged_files.append(path)
            if worktree_status not in (" ", "?") and worktree_status != " ":
                dirty_files.append(path)

    # Re-evaluate has_staged / has_unstaged against the ignored-file filter so
    # an ignored-only diff does not trip the gate.
    if has_unstaged and not dirty_files and not untracked_files:
        # Diff is non-empty but every changed file is ignored — treat clean.
        has_unstaged = False
    if has_staged and not staged_files:
        has_staged = False

    is_clean = not (has_staged or has_unstaged or untracked_files)
    blocker_code: Optional[str] = None
    blocker_message: Optional[str] = None
    if not is_clean:
        blocker_code = "blocked_dirty_repo"
        parts: List[str] = []
        if staged_files:
            parts.append(f"{len(staged_files)} staged")
        if dirty_files:
            parts.append(f"{len(dirty_files)} unstaged")
        if untracked_files:
            parts.append(f"{len(untracked_files)} untracked")
        blocker_message = (
            f"Repo {repo_path} has uncommitted changes: " + ", ".join(parts)
        )

    return RepoSafetyResult(
        repo_path=repo_path,
        is_clean=is_clean,
        dirty_files=dirty_files,
        staged_files=staged_files,
        untracked_files=untracked_files,
        current_branch=current_branch,
        current_commit=current_commit,
        blocker_code=blocker_code,
        blocker_message=blocker_message,
    )


def check_repo_busy(session: Session, repo_path: str) -> Optional[RepoLock]:
    """Return the active lock on ``repo_path``, or None if free."""
    return (
        session.query(RepoLock)
        .filter(
            RepoLock.repo_path == repo_path,
            RepoLock.lock_status == "active",
        )
        .one_or_none()
    )


def acquire_repo_lock(
    session: Session,
    repo_path: str,
    repo_name: str,
    branch_name: str,
    commit_sha: str,
    work_item_id: Optional[int] = None,
    task_id: Optional[str] = None,
    lock_owner: Optional[str] = None,
) -> LockResult:
    """Acquire an active lock on ``repo_path`` if none is held.

    Returns a :class:`LockResult` indicating success/failure. On failure the
    existing active lock is returned along with a blocker code/message so the
    caller can shape a 409.

    Because ``repo_locks.repo_path`` is a unique column, a row for the repo
    may already exist in a non-active state (e.g. the previous build's
    "released" record). In that case we recycle the row in place so the
    table stays "one row per repo, reflecting current state".
    """
    existing_any = (
        session.query(RepoLock).filter(RepoLock.repo_path == repo_path).one_or_none()
    )
    if existing_any is not None and existing_any.lock_status == "active":
        return LockResult(
            acquired=False,
            existing_lock=existing_any,
            blocker_code="blocked_repo_busy",
            blocker_message=(
                f"Repo {repo_path} is locked by an in-flight build "
                f"(lock id {existing_any.id})"
            ),
        )

    if existing_any is not None:
        # Recycle the prior row — preserves the unique repo_path constraint
        # while resetting the lifecycle fields for the new acquisition.
        existing_any.repo_name = repo_name
        existing_any.work_item_id = work_item_id
        existing_any.task_id = task_id
        existing_any.branch_name = branch_name
        existing_any.commit_sha = commit_sha
        existing_any.lock_owner = lock_owner
        existing_any.lock_status = "active"
        existing_any.started_at = datetime.utcnow()
        existing_any.released_at = None
        existing_any.release_reason = None
        session.commit()
        session.refresh(existing_any)
        return LockResult(acquired=True, lock=existing_any)

    lock = RepoLock(
        repo_path=repo_path,
        repo_name=repo_name,
        work_item_id=work_item_id,
        task_id=task_id,
        branch_name=branch_name,
        commit_sha=commit_sha,
        lock_owner=lock_owner,
        lock_status="active",
    )
    session.add(lock)
    session.commit()
    session.refresh(lock)
    return LockResult(acquired=True, lock=lock)


def release_repo_lock(
    session: Session,
    repo_path: str,
    release_reason: Optional[str] = None,
    final_status: str = "released",
) -> Optional[RepoLock]:
    """Release the active lock on ``repo_path``.

    Returns the updated row, or None when there was no active lock to release.
    ``final_status`` defaults to ``"released"``; callers can pass
    ``"failed"`` / ``"aborted"`` to keep the lock_status semantically distinct.
    """
    lock = check_repo_busy(session, repo_path)
    if lock is None:
        return None
    lock.lock_status = final_status
    lock.released_at = datetime.utcnow()
    if release_reason:
        # Truncate to fit the column. Better to lose tail than 500-error.
        lock.release_reason = release_reason[:200]
    session.commit()
    session.refresh(lock)
    return lock
