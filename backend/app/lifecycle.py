"""Workflow lifecycle helpers (slice 1).

Projects a work item's persisted state — Kanban status, debate progress,
operator approval, PR/code-review metadata, preview flags, certification, and
merge — into a single ``effective_state`` string that the UI can render
without re-deriving the rules.

This module is pure: it reads attributes from a ``WorkItem`` (and optionally a
"latest debate" summary) and returns a string. It performs no DB writes and no
side effects. Later slices may persist the result back to
``WorkItem.effective_state`` for indexing; for slice 1 the value is always
computed fresh per response.

State precedence (highest first)
--------------------------------
1. ``archived``         — archive flag wins over everything else.
2. ``merged``           — ``merge_commit_sha`` is set.
3. ``ready_to_merge``   — explicit operator gate.
4. ``certified``        — operator has signed off.
5. ``changes_requested``/``review_failed``/``code_reviewed`` — derived from
   ``code_review_status``.
6. ``preview_ready``    — preview has been deployed.
7. ``preview_pending``  — preview is required but not yet deployed.
8. ``in_review``        — a PR exists (number or URL) but no terminal review.
9. ``blocked``          — Kanban ``status`` is ``blocked``.
10. ``building``        — Kanban ``status`` indicates implementation in flight.
11. ``implemented``     — Kanban ``status`` indicates completion.
12. ``review_needed``   — Kanban ``status`` is review/review_needed.
13. ``approved``        — operator has approved but no builder activity yet.
14. ``debating``        — a debate run is currently active.
15. ``debated``         — a debate has completed and no later state applies.
16. ``drafting``        — fallback for fresh/draft items.
17. Otherwise: the raw Kanban status (lowercased) is returned so we never
    silently swallow an unknown state.
"""
from __future__ import annotations

from typing import Any, Optional


# Kanban statuses (case-insensitive) that mean "builder is actively working".
_BUILDING_STATUSES = frozenset({"active", "building", "in_progress"})

# Kanban statuses that mean "implementation finished".
_IMPLEMENTED_STATUSES = frozenset({"completed", "implemented", "done"})

# Kanban statuses that mean "awaiting human review" (without a code review
# verdict captured in ``code_review_status``).
_REVIEW_STATUSES = frozenset({"review", "review_needed"})

# Debate run statuses (matching ``DebateRun.status``) that are non-terminal.
_ACTIVE_DEBATE_STATUSES = frozenset(
    {"queued", "claimed", "warming", "running", "generating"}
)
_TERMINAL_DEBATE_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _norm(value: Any) -> Optional[str]:
    """Return a lowercase string for matching, or None for empty values."""
    if value is None:
        return None
    s = str(value).strip().lower()
    return s or None


def compute_effective_state(
    work_item: Any, latest_debate: Any = None
) -> str:
    """Compute the operator-facing effective state for a work item.

    Args:
        work_item: A ``WorkItem`` ORM instance (or any object exposing the
            same attributes). Read-only access — never mutated.
        latest_debate: Optional ``DebateRun``-like object. When provided, an
            active debate promotes the state to ``debating`` and a terminal
            debate to ``debated`` (only when no later signal — approval,
            build, review, PR, certification — applies).

    Returns:
        A short state string. See module docstring for the full precedence
        list.
    """
    # 1. Archived dominates: an archived item is not "in" any active state.
    if getattr(work_item, "archived", False):
        return "archived"

    # 2. Merged: a merge SHA is the terminal positive outcome.
    if _norm(getattr(work_item, "merge_commit_sha", None)):
        return "merged"

    # 3. Ready-to-merge gate (set by slice 5 once everything else passes).
    if getattr(work_item, "ready_to_merge", None) is True:
        return "ready_to_merge"

    # 4. Operator certification (slice 2).
    if getattr(work_item, "operator_certified", None) is True:
        return "certified"

    # 5. Code review verdict, when present, supersedes the generic PR state.
    code_review = _norm(getattr(work_item, "code_review_status", None))
    if code_review == "approved":
        return "code_reviewed"
    if code_review in ("changes_requested", "changes-requested"):
        return "changes_requested"
    if code_review == "failed":
        return "review_failed"

    # 6/7. Preview flags (slice 4 surfaces these).
    preview_required = getattr(work_item, "preview_required", None) is True
    preview_deployed = getattr(work_item, "preview_deployed", None) is True
    if preview_deployed:
        return "preview_ready"
    if preview_required:
        return "preview_pending"

    # 8. PR exists but no review/preview verdict yet.
    pr_number = getattr(work_item, "pr_number", None)
    pr_url = _norm(getattr(work_item, "pr_url", None))
    if pr_number or pr_url:
        return "in_review"

    # 9–12. Fall back to the Kanban status for build lifecycle states.
    status = _norm(getattr(work_item, "status", None))
    if status == "blocked":
        return "blocked"
    if status in _BUILDING_STATUSES:
        return "building"
    if status in _IMPLEMENTED_STATUSES:
        return "implemented"
    if status in _REVIEW_STATUSES:
        return "review_needed"

    # 13. Approval is checked after build/review states so that an approved
    # item which has since moved to "building" reports "building".
    if getattr(work_item, "approved_by_operator", False):
        return "approved"

    # 14–15. Debate signals only apply when nothing more advanced has happened.
    if latest_debate is not None:
        debate_status = _norm(getattr(latest_debate, "status", None))
        if debate_status in _ACTIVE_DEBATE_STATUSES:
            return "debating"
        if debate_status in _TERMINAL_DEBATE_STATUSES:
            return "debated"

    # 16. Draft fallback.
    if status in (None, "draft"):
        return "drafting"

    # 17. Unknown status — surface it verbatim rather than coercing to a
    # generic label, so operators can see exactly what the DB says.
    return status
