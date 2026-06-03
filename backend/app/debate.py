"""Debate workflow logic.

Responsibilities:

* Compute the input snapshot for a work item — the small set of fields a
  debate actually reads. Used both for de-duplication and as the
  ``input_snapshot_json`` audit trail on each run.
* Decide whether to auto-queue a new debate run for a work item (only when
  the item's status is debate-eligible AND its snapshot has changed since
  the latest run).
* Execute a debate run. The execution bridge is *not* configured in the
  dashboard; we mark the run ``queued`` with an explanatory message rather
  than fabricating arguments. The shape of the data is real even when the
  AI side is not wired up.
* Honour the safety rules: debate is advisory only. Nothing here mutates
  the work item, sets ``approved_by_operator``, or kicks off implementation.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from .models import (
    DEBATE_ELIGIBLE_STATUSES,
    DebateArgument,
    DebateRun,
    OperatorDebateInput,
    WorkItem,
)


# Fields a debate reads. Changing any of these invalidates the prior run's
# snapshot and lets a new automatic run queue. Editing only e.g. a PR URL
# or provenance does NOT trigger a re-debate.
SNAPSHOT_FIELDS = (
    "type",
    "title",
    "body",
    "target_app",
    "priority",
    "tags",
    "acceptance_notes",
)

# Keyword lists for deterministic stance classification.
# These are used when an operator submits an argument with
# stance_requested="auto_assign" so the system can place it on
# the correct side of the debate instead of defaulting to NEUTRAL.
_PRO_KEYWORDS = frozenset(
    {
        "support",
        "agree",
        "agree with",
        "good idea",
        "benefit",
        "benefits",
        "helps",
        "improves",
        "improve",
        "should do",
        "recommend",
        "recommendation",
        "approve",
        "approval",
        "favor",
        "in favour",
        "for",
        "pro",
        "positive",
        "valuable",
        "worthwhile",
        "strong case",
        "compelling",
        "makes sense",
        "right direction",
    }
)

_CON_KEYWORDS = frozenset(
    {
        "concern",
        "concerns",
        "oppose",
        "against",
        "risk",
        "risks",
        "danger",
        "warning",
        "objection",
        "disagree",
        "disagreement",
        "problem",
        "problems",
        "issue",
        "issues",
        "flaw",
        "flawed",
        "reject",
        "rejection",
        "negative",
        "drawback",
        "downside",
        "pitfall",
        "caution",
        "beware",
        "warning",
        "should not",
        "must not",
        "avoid",
        "prevent",
        "block",
        "blocking",
        "critical",
        "severe",
    }
)


def classify_operator_stance(content: str) -> str:
    """Classify an operator argument's stance relative to the work item.

    Uses a simple keyword heuristic to determine whether the argument
    supports (PRO), opposes/constrains (CON), or merely contextualises
    (NEUTRAL) the work item.

    This is intentionally lightweight — the debate LLM will refine the
    position during execution. The goal is to avoid defaulting every
    auto_assign argument to NEUTRAL.

    Returns:
        "pro" if the argument clearly supports the work item.
        "con" if the argument clearly opposes or constrains the work item.
        "neutral" if the argument provides background context without
        a clear supporting or opposing position.
    """
    text = content.lower()

    # Score by counting keyword matches (each keyword counted once at most)
    pro_score = sum(1 for kw in _PRO_KEYWORDS if kw in text)
    con_score = sum(1 for kw in _CON_KEYWORDS if kw in text)

    # Require a minimum threshold to avoid spurious classification
    # from incidental keyword matches.
    if pro_score >= 2 and pro_score > con_score:
        return "pro"
    if con_score >= 2 and con_score > pro_score:
        return "con"
    # Single strong signal: if only one type of keyword appears and
    # the other is zero, classify even with score=1.
    if pro_score == 1 and con_score == 0:
        return "pro"
    if con_score == 1 and pro_score == 0:
        return "con"
    return "neutral"


def is_debate_eligible(work_item: WorkItem) -> bool:
    return work_item.status in DEBATE_ELIGIBLE_STATUSES


def snapshot_payload(work_item: WorkItem) -> dict:
    return {field: getattr(work_item, field, None) for field in SNAPSHOT_FIELDS}


def snapshot_json(work_item: WorkItem) -> str:
    return json.dumps(snapshot_payload(work_item), sort_keys=True, default=str)


def _latest_run(db: Session, work_item_id: int) -> Optional[DebateRun]:
    return (
        db.query(DebateRun)
        .filter(DebateRun.work_item_id == work_item_id)
        .order_by(DebateRun.id.desc())
        .first()
    )


def _diff_snapshots(previous_json: Optional[str], current_json: str) -> Optional[dict]:
    if not previous_json:
        return None
    try:
        previous = json.loads(previous_json)
    except (TypeError, ValueError):
        return None
    current = json.loads(current_json)
    changed = {}
    for key in SNAPSHOT_FIELDS:
        if previous.get(key) != current.get(key):
            changed[key] = {"from": previous.get(key), "to": current.get(key)}
    return changed or None


def execution_bridge_configured(db: Session) -> bool:
    """Check if debate execution is enabled via DB settings.

    DB/UI-managed settings are authoritative. Environment variables
    are bootstrap-only and not used for normal runtime configuration.

    Returns True if settings.enabled=True and a model host/model is configured.
    """
    from .debate_executor import get_execution_config
    config = get_execution_config(db)
    return bool(config.enabled) and bool(config.base_url) and bool(config.model)


def queue_debate_run(
    db: Session,
    work_item: WorkItem,
    *,
    trigger: str,
    rounds: int,
    force: bool = False,
) -> Optional[DebateRun]:
    """Queue a new ``DebateRun`` for the given work item, or return None when
    a run for the current snapshot already exists.

    ``force=True`` is used by manual reruns and operator-requested runs; it
    bypasses the snapshot de-duplication so the operator can always start a
    fresh run.
    """
    current_snapshot = snapshot_json(work_item)
    previous = _latest_run(db, work_item.id)

    if not force and previous is not None:
        # An identical snapshot already has a run on file — refuse to
        # duplicate it.
        if previous.input_snapshot_json == current_snapshot:
            return None

    diff = _diff_snapshots(
        previous.input_snapshot_json if previous else None, current_snapshot
    )

    run = DebateRun(
        work_item_id=work_item.id,
        work_item_type_snapshot=work_item.type,
        status="queued",
        rounds_requested=rounds,
        rounds_completed=0,
        trigger=trigger,
        input_snapshot_json=current_snapshot,
        changed_since_previous_json=json.dumps(diff) if diff else None,
    )
    db.add(run)
    db.flush()  # populate run.id without committing — caller commits

    if not execution_bridge_configured(db):
        # Record the queued-but-not-executed state with clear messaging.
        # This is shown in the UI so the operator knows why the run hasn't
        # executed and can enable execution in Settings.
        note = DebateArgument(
            debate_run_id=run.id,
            round_number=0,
            role="System",
            side="neutral",
            content=(
                "Debate execution is disabled in Settings. "
                "Enable it at Settings > Debate Execution."
            ),
        )
        db.add(note)
        run.provenance = "execution-disabled"

    return run


def queue_debate_for_eligible_item(
    db: Session, work_item: WorkItem, *, rounds: int = 2
) -> Optional[DebateRun]:
    """Auto-queue helper used by the work-items router on create / status
    transition. Returns the new run, or ``None`` if the item is not
    debate-eligible or if a run for this snapshot already exists.
    """
    if not is_debate_eligible(work_item):
        return None
    return queue_debate_run(
        db,
        work_item,
        trigger="automatic",
        rounds=rounds,
        force=False,
    )


def list_pending_operator_inputs(
    db: Session, work_item_id: int
) -> list[OperatorDebateInput]:
    """Operator arguments not yet attached to any run."""
    return (
        db.query(OperatorDebateInput)
        .filter(
            OperatorDebateInput.work_item_id == work_item_id,
            OperatorDebateInput.considered_in_run_id.is_(None),
        )
        .order_by(OperatorDebateInput.id.asc())
        .all()
    )


def attach_operator_inputs_to_run(
    db: Session,
    run: DebateRun,
    inputs: Iterable[OperatorDebateInput],
) -> None:
    """Link operator inputs to a run and surface them as ``DebateArgument``
    rows so the UI renders them alongside model output. We do this even when
    the execution bridge is unconfigured — operator arguments are real
    operator content, not model output, and showing them is the whole point
    of letting the operator add them.

    Auto-assigned operator arguments (stance_requested=auto_assign) are
    classified into PRO/CON/NEUTRAL based on their content before being
    attached to the run. Manual stances are preserved as-is.
    """
    for op_input in inputs:
        if op_input.stance_requested == "auto_assign":
            # Classify the argument content to determine stance.
            assigned = classify_operator_stance(op_input.content)
        else:
            # Manual stance: preserve the operator's explicit choice.
            assigned = op_input.stance_requested

        op_input.considered_in_run_id = run.id
        op_input.stance_assigned = assigned

        db.add(
            DebateArgument(
                debate_run_id=run.id,
                round_number=0,
                role="Operator",
                side=assigned,
                content=op_input.content,
            )
        )


def mark_run_failed(run: DebateRun, message: str) -> None:
    run.status = "failed"
    run.error_message = message
    run.completed_at = datetime.utcnow()
