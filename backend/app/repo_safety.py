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

from sqlalchemy.exc import IntegrityError
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

    def _git_inspection_failed(cmd: str, proc: subprocess.CompletedProcess) -> RepoSafetyResult:
        err = (proc.stderr or "").strip() or f"git exited {proc.returncode}"
        return RepoSafetyResult(
            repo_path=repo_path,
            is_clean=False,
            current_branch=current_branch,
            current_commit=current_commit,
            blocker_code="git_inspection_failed",
            blocker_message=f"Cannot inspect repo ({cmd}): {err[:200]}",
        )

    # Unstaged changes — `git diff --quiet` exits 0 (clean), 1 (changes),
    # anything else (e.g. 128 "not a git repo", 129 usage error) is a real
    # git failure that we must NOT treat as "dirty"; block as
    # ``git_inspection_failed`` so the operator sees the underlying error
    # instead of a misleading dirty-repo report.
    unstaged_proc = _run_git(repo_path, ["diff", "--quiet"])
    if unstaged_proc.returncode not in (0, 1):
        return _git_inspection_failed("git diff --quiet", unstaged_proc)
    has_unstaged = unstaged_proc.returncode == 1

    # Staged changes — same 0/1/other convention as above.
    staged_proc = _run_git(repo_path, ["diff", "--cached", "--quiet"])
    if staged_proc.returncode not in (0, 1):
        return _git_inspection_failed("git diff --cached --quiet", staged_proc)
    has_staged = staged_proc.returncode == 1

    # Untracked, non-ignored files. ls-files only returns non-zero on error.
    untracked_proc = _run_git(
        repo_path, ["ls-files", "--others", "--exclude-standard"]
    )
    if untracked_proc.returncode != 0:
        return _git_inspection_failed("git ls-files --others", untracked_proc)
    untracked_raw = [
        line for line in untracked_proc.stdout.splitlines() if line.strip()
    ]
    untracked_files = _filter_ignored(untracked_raw)

    # Parse `git status --short` to produce dirty/staged file lists. Format:
    # ``XY path`` where X = index status, Y = worktree status. Non-zero
    # here means we cannot trust the dirty/staged split — block.
    status_proc = _run_git(repo_path, ["status", "--short"])
    if status_proc.returncode != 0:
        return _git_inspection_failed("git status --short", status_proc)
    dirty_files: List[str] = []
    staged_files: List[str] = []
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


def check_repo_clean_via_executor(repo_path: str) -> RepoSafetyResult:
    """Same contract as :func:`check_repo_clean`, but delegates to the host
    executor.

    The dashboard container mounts ``/srv/repo`` read-only and is not a
    valid git worktree, so running ``git status`` inside the container
    fails with ``fatal: not a git repository`` and the original
    in-container gate misreports every Start Build as
    ``git_inspection_failed``. The host executor already owns the side-
    effectful workflow path (deploy_preview / revert_preview / merge_pr);
    this helper reuses the same channel for a read-only inspection so the
    gate sees the worktree the way the host sees it.

    Fails closed: a non-success executor response (unreachable, HTTP error,
    malformed JSON) yields ``is_clean=False`` with the executor's
    ``error_code`` (or ``executor_unreachable``) so a misconfigured
    executor never lets a build slip past the safety gate.
    """
    # Local import keeps ``preview_deploy`` -> ``repo_safety`` imports from
    # ever circling back. ``preview_deploy`` does not import ``repo_safety``
    # today, but pinning this at function scope avoids a future regression.
    from . import preview_deploy

    response = preview_deploy.call_host_executor(
        action="repo_safety_check",
        repo_path=repo_path,
    )

    if not response.success:
        return RepoSafetyResult(
            repo_path=repo_path,
            is_clean=False,
            blocker_code=response.error_code or "executor_unreachable",
            blocker_message=(
                response.error
                or f"Host executor cannot inspect repo {repo_path}"
            ),
        )

    data = response.raw or {}
    return RepoSafetyResult(
        repo_path=repo_path,
        is_clean=bool(data.get("is_clean")),
        dirty_files=list(data.get("dirty_files") or []),
        staged_files=list(data.get("staged_files") or []),
        untracked_files=list(data.get("untracked_files") or []),
        current_branch=data.get("current_branch") or None,
        current_commit=data.get("current_commit") or None,
        blocker_code=data.get("blocker_code") or None,
        blocker_message=data.get("blocker_message") or None,
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
        # Recycle the prior row atomically: the UPDATE's WHERE clause re-asserts
        # the non-active state we read into ``existing_any``. If a concurrent
        # caller already flipped the row to ``active`` between our SELECT and
        # this UPDATE, rowcount will be 0 — we surface the same
        # ``blocked_repo_busy`` outcome callers handle for the early-active
        # branch instead of admitting two owners.
        now = datetime.utcnow()
        updated = (
            session.query(RepoLock)
            .filter(
                RepoLock.id == existing_any.id,
                RepoLock.lock_status != "active",
            )
            .update(
                {
                    RepoLock.repo_name: repo_name,
                    RepoLock.work_item_id: work_item_id,
                    RepoLock.task_id: task_id,
                    RepoLock.branch_name: branch_name,
                    RepoLock.commit_sha: commit_sha,
                    RepoLock.lock_owner: lock_owner,
                    RepoLock.lock_status: "active",
                    RepoLock.started_at: now,
                    RepoLock.released_at: None,
                    RepoLock.release_reason: None,
                },
                synchronize_session=False,
            )
        )
        if updated == 0:
            session.rollback()
            existing = (
                session.query(RepoLock)
                .filter(RepoLock.repo_path == repo_path)
                .one_or_none()
            )
            return LockResult(
                acquired=False,
                existing_lock=existing,
                blocker_code="blocked_repo_busy",
                blocker_message=(
                    f"Repo {repo_path} was locked by a concurrent build "
                    "during recycle"
                ),
            )
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
    try:
        session.commit()
    except IntegrityError:
        # Concurrent acquirer beat us to the unique repo_path constraint.
        # Surface this as the same blocker callers handle for an existing
        # active lock, rather than letting the constraint violation become
        # a 500 at the API edge.
        session.rollback()
        existing = (
            session.query(RepoLock)
            .filter(RepoLock.repo_path == repo_path)
            .one_or_none()
        )
        return LockResult(
            acquired=False,
            existing_lock=existing,
            blocker_code="blocked_repo_busy",
            blocker_message=(
                f"Repo {repo_path} was locked by a concurrent build "
                "during acquire"
            ),
        )
    session.refresh(lock)
    return LockResult(acquired=True, lock=lock)


def release_repo_lock(
    session: Session,
    repo_path: str,
    release_reason: Optional[str] = None,
    final_status: str = "released",
    expected_task_id: Optional[str] = None,
) -> Optional[RepoLock]:
    """Release the active lock on ``repo_path``.

    Returns the updated row, or None when there was no active lock to release
    (either because no lock exists, or because the active lock is owned by a
    different task than ``expected_task_id``).

    ``final_status`` defaults to ``"released"``; callers can pass
    ``"failed"`` / ``"aborted"`` to keep the lock_status semantically distinct.

    ``expected_task_id`` guards against a stale terminal-status sync from an
    older builder run releasing the *new* build's lock. When provided, the
    release only proceeds if the active lock's ``task_id`` matches. The
    manual-clear endpoint omits this so an operator can always force-release.
    """
    lock = check_repo_busy(session, repo_path)
    if lock is None:
        return None
    if expected_task_id is not None and lock.task_id != expected_task_id:
        # Lock belongs to a different task — don't release someone else's lock.
        return None
    lock.lock_status = final_status
    lock.released_at = datetime.utcnow()
    if release_reason:
        # Truncate to fit the column. Better to lose tail than 500-error.
        lock.release_reason = release_reason[:200]
    session.commit()
    session.refresh(lock)
    return lock
