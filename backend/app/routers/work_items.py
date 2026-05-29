from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import FollowUp, WorkItem
from ..schemas import (
    BlockRequest,
    FollowUpCreate,
    FollowUpResponse,
    WorkItemCreate,
    WorkItemResponse,
    WorkItemUpdate,
)

router = APIRouter(prefix="/api/work-items", tags=["work-items"])


def _get_or_404(db: Session, work_item_id: int) -> WorkItem:
    item = db.query(WorkItem).filter(WorkItem.id == work_item_id).first()
    if item is None:
        raise HTTPException(status_code=404, detail="Work item not found")
    return item


@router.get("", response_model=List[WorkItemResponse])
def list_work_items(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(WorkItem)
    if type is not None:
        query = query.filter(WorkItem.type == type)
    if status is not None:
        query = query.filter(WorkItem.status == status)
    return query.order_by(WorkItem.id.desc()).all()


@router.post("", response_model=WorkItemResponse, status_code=status.HTTP_201_CREATED)
def create_work_item(payload: WorkItemCreate, db: Session = Depends(get_db)):
    item = WorkItem(**payload.model_dump(exclude_unset=True))
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/{work_item_id}", response_model=WorkItemResponse)
def get_work_item(work_item_id: int, db: Session = Depends(get_db)):
    return _get_or_404(db, work_item_id)


@router.put("/{work_item_id}", response_model=WorkItemResponse)
def update_work_item(
    work_item_id: int,
    payload: WorkItemUpdate,
    db: Session = Depends(get_db),
):
    item = _get_or_404(db, work_item_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@router.post("/{work_item_id}/approve", response_model=WorkItemResponse)
def approve_work_item(work_item_id: int, db: Session = Depends(get_db)):
    item = _get_or_404(db, work_item_id)
    item.approved_by_operator = True
    item.approval_timestamp = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return item


@router.post("/{work_item_id}/block", response_model=WorkItemResponse)
def block_work_item(
    work_item_id: int,
    payload: Optional[BlockRequest] = None,
    db: Session = Depends(get_db),
):
    item = _get_or_404(db, work_item_id)
    item.status = "blocked"
    if payload and payload.override_reason is not None:
        item.override_reason = payload.override_reason
        item.override_timestamp = datetime.utcnow()
    db.commit()
    db.refresh(item)
    return item


@router.get("/{work_item_id}/follow-ups", response_model=List[FollowUpResponse])
def list_follow_ups(work_item_id: int, db: Session = Depends(get_db)):
    _get_or_404(db, work_item_id)
    return (
        db.query(FollowUp)
        .filter(FollowUp.work_item_id == work_item_id)
        .order_by(FollowUp.id.desc())
        .all()
    )


@router.post(
    "/{work_item_id}/follow-ups",
    response_model=FollowUpResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_follow_up(
    work_item_id: int,
    payload: FollowUpCreate,
    db: Session = Depends(get_db),
):
    _get_or_404(db, work_item_id)
    follow_up = FollowUp(work_item_id=work_item_id, **payload.model_dump(exclude_unset=True))
    db.add(follow_up)
    db.commit()
    db.refresh(follow_up)
    return follow_up
