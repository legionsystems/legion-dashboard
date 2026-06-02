"""Repo safety / lock endpoints (workflow slice 3).

Operator-facing read API for the build gate:

* ``GET /api/repo-safety/check?repo_path=...`` — inspect a repo and return
  whether it would currently pass the gate.
* ``GET /api/repo-locks`` — list active repo locks.
* ``DELETE /api/repo-locks/{id}`` — manually release a stale lock.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import RepoLock
from .. import repo_safety
from ..schemas import RepoLockResponse, RepoSafetyResult


router = APIRouter(tags=["repo-safety"])


@router.get("/api/repo-safety/check", response_model=RepoSafetyResult)
def check_repo(repo_path: str = Query(..., min_length=1)):
    """Return the safety-gate state for ``repo_path``."""
    return repo_safety.check_repo_clean(repo_path)


@router.get("/api/repo-locks", response_model=List[RepoLockResponse])
def list_repo_locks(
    status: Optional[str] = Query(
        None,
        description="Filter by lock_status. Defaults to 'active' only.",
    ),
    db: Session = Depends(get_db),
):
    """List repo locks. By default, returns only active locks."""
    query = db.query(RepoLock)
    if status is None:
        query = query.filter(RepoLock.lock_status == "active")
    else:
        query = query.filter(RepoLock.lock_status == status)
    return query.order_by(RepoLock.started_at.desc()).all()


@router.delete("/api/repo-locks/{lock_id}", response_model=RepoLockResponse)
def release_lock_manually(
    lock_id: int,
    db: Session = Depends(get_db),
):
    """Manually release a stale lock by id.

    Returns the updated row. Releasing an already-released lock is a no-op
    and still returns 200 with the existing row so the UI can refresh.
    """
    lock = db.query(RepoLock).filter(RepoLock.id == lock_id).one_or_none()
    if lock is None:
        raise HTTPException(status_code=404, detail="Repo lock not found")
    if lock.lock_status == "active":
        repo_safety.release_repo_lock(
            db,
            lock.repo_path,
            release_reason="manual_clear",
            final_status="released",
        )
        db.refresh(lock)
    return lock
