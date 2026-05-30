from typing import Dict, List

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import WorkItem
from ..schemas import StatsResponse, StatsRecentItem

router = APIRouter(prefix="/api/stats", tags=["stats"])


STATUS_KEYS = [
    "draft",
    "debated",
    "approved",
    "active",
    "review_needed",
    "certified",
    "pr_open",
    "ready_for_merge",
    "merged",
    "blocked",
    "completed",
]

TYPE_KEYS = ["idea", "bug", "note", "task", "slice"]


def _zeroed(keys: List[str]) -> Dict[str, int]:
    return {key: 0 for key in keys}


@router.get("", response_model=StatsResponse)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    status_counts = _zeroed(STATUS_KEYS)
    for status_value, count in (
        db.query(WorkItem.status, func.count(WorkItem.id))
        .group_by(WorkItem.status)
        .all()
    ):
        status_counts[status_value] = (status_counts.get(status_value, 0)) + count

    type_counts = _zeroed(TYPE_KEYS)
    for type_value, count in (
        db.query(WorkItem.type, func.count(WorkItem.id))
        .group_by(WorkItem.type)
        .all()
    ):
        type_counts[type_value] = (type_counts.get(type_value, 0)) + count

    total = db.query(func.count(WorkItem.id)).scalar() or 0
    awaiting_approval = (
        db.query(func.count(WorkItem.id))
        .filter(WorkItem.approved_by_operator.is_(False))
        .scalar()
        or 0
    )

    recent = (
        db.query(WorkItem)
        .order_by(WorkItem.updated_at.desc(), WorkItem.id.desc())
        .limit(8)
        .all()
    )
    recent_items = [
        StatsRecentItem(
            id=item.id,
            type=item.type,
            title=item.title,
            status=item.status,
            updated_at=item.updated_at,
        )
        for item in recent
    ]

    return StatsResponse(
        total=total,
        awaiting_approval=awaiting_approval,
        by_status=status_counts,
        by_type=type_counts,
        recent=recent_items,
    )
