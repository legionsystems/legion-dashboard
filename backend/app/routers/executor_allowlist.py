"""Executor allowlist management endpoints.

Surfaces the host preview executor's ``ALLOWED_REPO_ROOTS`` config in the
LEGION Dashboard. The dashboard owns the persisted list (via the
``ExecutorAllowlistRoot`` rows seeded at migration time with the three
baked-in defaults); operators add new roots through the UI; the ``apply``
endpoint writes the joined enabled list out to the executor's
``EnvironmentFile=`` and triggers ``systemctl restart legion-preview-executor``.

Hard constraints encoded here so the UI cannot smuggle a request past the
trust boundary the executor already enforces:

* Every path must be absolute, have no ``..`` segments, and not be a
  symlink. Bare blacklist of ``/``, ``/root``, ``/etc``, ``/tmp``,
  ``/var``, ``/usr``, ``/bin``, ``/sbin``, ``/proc``, ``/sys``, ``/dev``,
  ``/boot`` and ``/home`` so a stray operator click cannot widen the
  surface to arbitrary filesystem locations.
* Default roots cannot be deleted, only disabled.
* Duplicate paths are rejected at the schema layer (unique constraint).
"""
from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import PurePosixPath
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ExecutorAllowlistApplyLog, ExecutorAllowlistRoot
from ..schemas import (
    ExecutorAllowlistApplyRequest,
    ExecutorAllowlistConfigResponse,
    ExecutorAllowlistRootCreate,
    ExecutorAllowlistRootRead,
    ExecutorAllowlistRootUpdate,
)


router = APIRouter(prefix="/api/executor-allowlist", tags=["executor-allowlist"])


# ---------------------------------------------------------------------------
# Defaults / constants
# ---------------------------------------------------------------------------


DEFAULT_ALLOWLIST_ROOTS = (
    "/srv/repo/legion-dashboard",
    "/srv/repo/lgn-hub",
    "/srv/worktrees/legion-dashboard",
)

# Disallowed top-level paths. A request to add any of these (or anything
# rooted directly at them) is rejected with 400. The defaults under
# ``/srv`` remain reachable because none of these prefixes match them.
_FORBIDDEN_PREFIXES = (
    "/root",
    "/etc",
    "/tmp",
    "/var",
    "/usr",
    "/bin",
    "/sbin",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/home",
)


def _default_config_path() -> str:
    """Where the joined allowlist gets written on apply.

    Overridable via ``LEGION_EXECUTOR_ALLOWLIST_CONFIG_PATH`` so tests can
    point at ``tmp_path`` and operators can move the file if they re-wire
    the systemd ``EnvironmentFile=``.
    """
    return os.environ.get(
        "LEGION_EXECUTOR_ALLOWLIST_CONFIG_PATH",
        "/etc/legion/executor-allowlist.conf",
    )


def _systemctl_command() -> List[str]:
    """The restart command. Tests monkey-patch ``_run_restart``; this
    isolates the actual binary path so an operator can override via env
    without code changes (some hosts use ``sudo systemctl ...``)."""
    raw = os.environ.get(
        "LEGION_EXECUTOR_ALLOWLIST_RESTART_CMD",
        "/usr/bin/systemctl restart legion-preview-executor",
    )
    return raw.split()


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _validate_path(raw: str) -> str:
    """Normalize and validate a candidate allowlist path.

    Returns the canonical absolute path string on success. Raises
    ``HTTPException(400)`` with a short reason on any rejection.
    """
    if not isinstance(raw, str):
        raise HTTPException(status_code=400, detail="path must be a string")
    candidate = raw.strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="path must be non-empty")
    if not candidate.startswith("/"):
        raise HTTPException(
            status_code=400,
            detail="path must be absolute (start with '/')",
        )
    if "\x00" in candidate:
        raise HTTPException(
            status_code=400, detail="path contains a NUL byte"
        )
    # Reject ``..`` segments before any filesystem touch so a path that
    # doesn't yet exist still gets caught.
    parts = PurePosixPath(candidate).parts
    if ".." in parts:
        raise HTTPException(
            status_code=400,
            detail="path must not contain '..' segments",
        )
    # Collapse repeated slashes / trailing slashes.
    normalized = str(PurePosixPath(candidate))
    if normalized in ("/", ""):
        raise HTTPException(
            status_code=400, detail="path must not be the filesystem root"
        )
    # Forbidden tops: reject anything that is — or is rooted under —
    # /root, /etc, /tmp, etc. ``startswith`` with a trailing ``/`` so
    # ``/etcetera`` is not falsely flagged.
    for forbidden in _FORBIDDEN_PREFIXES:
        if normalized == forbidden or normalized.startswith(forbidden + "/"):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"path must not be under {forbidden}/ — the executor "
                    "trust boundary forbids that prefix"
                ),
            )
    # Symlink escape: if the path exists on disk and resolves to a
    # different canonical location, refuse it. A non-existent path is
    # allowed (operator may stage a root before the directory is created).
    try:
        if os.path.lexists(normalized):
            real = os.path.realpath(normalized)
            if real != normalized:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "path resolves through a symlink "
                        f"({normalized!r} -> {real!r}); use the realpath"
                    ),
                )
    except OSError:
        # realpath itself failed — refuse rather than silently accept.
        raise HTTPException(
            status_code=400, detail="path could not be canonicalized"
        )
    return normalized


# ---------------------------------------------------------------------------
# Config file format
# ---------------------------------------------------------------------------


def _format_config_file(roots: List[ExecutorAllowlistRoot]) -> str:
    """Render the joined enabled allowlist as a systemd EnvironmentFile.

    The file is a valid ``EnvironmentFile=`` for the
    ``legion-preview-executor`` unit: a header block of comment lines (one
    per root, with note + default tag for the operator to audit) followed
    by a single ``LEGION_EXECUTOR_ALLOWED_REPO_ROOTS=<csv>`` assignment.
    """
    enabled = [r for r in roots if r.is_enabled]
    lines: List[str] = []
    lines.append(
        "# LEGION executor allowlist — managed by the dashboard."
    )
    lines.append(
        "# Source: /api/executor-allowlist (see /settings/executor-allowlist)."
    )
    lines.append(f"# Generated: {datetime.utcnow().isoformat()}Z")
    lines.append(
        "# DO NOT EDIT BY HAND — changes are overwritten on the next apply."
    )
    lines.append("#")
    if not enabled:
        lines.append("# (no enabled roots — executor will fall back to source defaults)")
    else:
        for root in enabled:
            tag = " [default]" if root.is_default else ""
            note = f" — {root.note}" if root.note else ""
            lines.append(f"# {root.path}{tag}{note}")
    lines.append("")
    joined = ",".join(r.path for r in enabled)
    lines.append(f"LEGION_EXECUTOR_ALLOWED_REPO_ROOTS={joined}")
    lines.append("")
    return "\n".join(lines)


def _joined_enabled(roots: List[ExecutorAllowlistRoot]) -> str:
    return ",".join(r.path for r in roots if r.is_enabled)


# ---------------------------------------------------------------------------
# Config file write + systemctl restart
# ---------------------------------------------------------------------------


def _write_config_file(path: str, contents: str) -> None:
    """Atomic write: tmp + rename so a partial write never replaces the
    file. Creates the parent directory on first apply."""
    parent = os.path.dirname(path) or "/"
    if not os.path.isdir(parent):
        os.makedirs(parent, mode=0o755, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(contents)
    os.replace(tmp, path)


def _run_restart(cmd: List[str], timeout: int = 30) -> tuple[int, str, str]:
    """Run the systemd restart. Isolated for monkey-patching in tests."""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Seed defaults (used by tests + lifespan-on-fresh-db)
# ---------------------------------------------------------------------------


def seed_default_allowlist_roots(db: Session) -> None:
    """Ensure the three baked-in defaults exist as ``is_default = True``
    rows. Idempotent — safe to call on every startup.

    Production runs alembic which seeds in the migration; tests use
    ``Base.metadata.create_all`` so the migration never runs and this
    helper is the only seeder.
    """
    existing = {r.path for r in db.query(ExecutorAllowlistRoot).all()}
    for path in DEFAULT_ALLOWLIST_ROOTS:
        if path in existing:
            continue
        db.add(
            ExecutorAllowlistRoot(
                path=path,
                added_by="system",
                note="Seeded default root (matches executor source).",
                is_default=True,
                is_enabled=True,
            )
        )
    db.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_roots_ordered(db: Session) -> List[ExecutorAllowlistRoot]:
    return (
        db.query(ExecutorAllowlistRoot)
        .order_by(
            ExecutorAllowlistRoot.is_default.desc(),
            ExecutorAllowlistRoot.added_at.asc(),
            ExecutorAllowlistRoot.id.asc(),
        )
        .all()
    )


def _last_apply(db: Session) -> Optional[ExecutorAllowlistApplyLog]:
    return (
        db.query(ExecutorAllowlistApplyLog)
        .order_by(ExecutorAllowlistApplyLog.applied_at.desc())
        .first()
    )


def _on_disk_joined(path: str) -> Optional[str]:
    """Read the joined CSV currently in the on-disk config file, or None
    if the file is absent / malformed. Used to compute ``pending_apply``."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("LEGION_EXECUTOR_ALLOWED_REPO_ROOTS="):
                    return line.split("=", 1)[1]
    except OSError:
        return None
    return None


def _build_config_response(db: Session) -> ExecutorAllowlistConfigResponse:
    roots = _all_roots_ordered(db)
    config_path = _default_config_path()
    desired = _joined_enabled(roots)
    on_disk = _on_disk_joined(config_path)
    pending = desired != (on_disk or "")
    last = _last_apply(db)
    env_override = os.environ.get("LEGION_EXECUTOR_ALLOWED_REPO_ROOTS", "")
    return ExecutorAllowlistConfigResponse(
        roots=[ExecutorAllowlistRootRead.model_validate(r) for r in roots],
        pending_apply=pending,
        last_apply_at=last.applied_at if last is not None else None,
        last_apply_error=last.error if last is not None else None,
        last_apply_restart_ok=last.restart_ok if last is not None else None,
        config_path=config_path,
        env_override_active=bool(env_override.strip()),
        env_override_value=env_override or None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ExecutorAllowlistConfigResponse)
def get_executor_allowlist(db: Session = Depends(get_db)):
    """Return the persisted allowlist plus apply / override status."""
    # Backfill defaults on the first read so an install that pre-dates the
    # migration (or a test using create_all) still sees the seeded roots.
    if db.query(ExecutorAllowlistRoot).count() == 0:
        seed_default_allowlist_roots(db)
    return _build_config_response(db)


@router.post(
    "",
    response_model=ExecutorAllowlistRootRead,
    status_code=status.HTTP_201_CREATED,
)
def add_executor_allowlist_root(
    payload: ExecutorAllowlistRootCreate,
    db: Session = Depends(get_db),
):
    """Stage a new allowlist root. Not applied to the running executor
    until ``POST /apply``."""
    canonical = _validate_path(payload.path)
    existing = (
        db.query(ExecutorAllowlistRoot)
        .filter(ExecutorAllowlistRoot.path == canonical)
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"path {canonical!r} is already in the allowlist",
        )
    row = ExecutorAllowlistRoot(
        path=canonical,
        added_by=payload.added_by,
        note=payload.note,
        is_default=False,
        is_enabled=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{root_id}", response_model=ExecutorAllowlistRootRead)
def update_executor_allowlist_root(
    root_id: int,
    payload: ExecutorAllowlistRootUpdate,
    db: Session = Depends(get_db),
):
    """Update ``is_enabled`` / ``note`` on a single row."""
    row = (
        db.query(ExecutorAllowlistRoot)
        .filter(ExecutorAllowlistRoot.id == root_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="allowlist root not found")
    if payload.is_enabled is not None:
        # Defaults can be disabled (operator may want to drop /srv/repo/lgn-hub
        # on a single-app host). Re-enabling a default is allowed too: it is a
        # harmless restoration of the seeded state.
        row.is_enabled = payload.is_enabled
    if payload.note is not None:
        row.note = payload.note
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{root_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_executor_allowlist_root(
    root_id: int, db: Session = Depends(get_db)
):
    """Delete a non-default row. Defaults must be disabled via PATCH
    rather than removed so a fresh install never loses its built-in
    coverage."""
    row = (
        db.query(ExecutorAllowlistRoot)
        .filter(ExecutorAllowlistRoot.id == root_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="allowlist root not found")
    if row.is_default:
        raise HTTPException(
            status_code=409,
            detail=(
                "default roots cannot be removed; disable via PATCH "
                "if you want the executor to ignore this entry"
            ),
        )
    db.delete(row)
    db.commit()
    return None


@router.post(
    "/apply",
    response_model=ExecutorAllowlistConfigResponse,
)
def apply_executor_allowlist(
    payload: ExecutorAllowlistApplyRequest,
    db: Session = Depends(get_db),
):
    """Write the joined enabled allowlist to disk and restart the executor.

    The write is atomic (tmp + rename); the restart is best-effort and its
    outcome is recorded on the audit row even if the dashboard cannot
    actually reach the host's systemd (e.g. inside a container without
    privileged access). The persisted DB state is the source of truth — a
    failed apply does not roll back the DB, the operator can fix the
    environment and retry.
    """
    if not payload.confirm:
        raise HTTPException(
            status_code=400,
            detail="apply requires confirm=true (the executor will restart)",
        )
    roots = _all_roots_ordered(db)
    contents = _format_config_file(roots)
    config_path = _default_config_path()
    joined = _joined_enabled(roots)
    error: Optional[str] = None
    restart_ok = False
    try:
        _write_config_file(config_path, contents)
    except OSError as exc:
        error = f"config write failed: {exc}"
    if error is None:
        try:
            rc, _stdout, stderr = _run_restart(_systemctl_command())
            if rc == 0:
                restart_ok = True
            else:
                # First 400 chars of stderr — enough to diagnose, bounded
                # so an angry systemd message can't blow up the audit row.
                tail = (stderr or "").strip()[:400]
                error = f"systemctl restart exited {rc}: {tail or '(no stderr)'}"
        except FileNotFoundError as exc:
            error = f"systemctl not available: {exc}"
        except subprocess.TimeoutExpired:
            error = "systemctl restart timed out"
        except OSError as exc:
            error = f"systemctl restart failed: {exc}"
    db.add(
        ExecutorAllowlistApplyLog(
            applied_by=payload.applied_by,
            joined_roots=joined,
            config_path=config_path,
            restart_ok=restart_ok,
            error=error,
        )
    )
    db.commit()
    return _build_config_response(db)
