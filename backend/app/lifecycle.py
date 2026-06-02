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
2. ``complete``         — operator has acknowledged a verified post-merge
   deploy (slice 5). Terminal positive outcome.
3. ``merged_deployment_failed`` — PR is merged but the post-merge
   healthcheck failed (slice 5). The operator must fix the deploy before
   completing.
4. ``merged``           — ``merge_commit_sha`` is set.
5. ``blocked_merge``    — operator attempted a merge and the executor
   refused / failed before any SHA was recorded (slice 5).
6. ``ready_to_merge``   — explicit operator gate.
7. ``certified``        — operator has signed off.
5. ``rejected``         — operator rejected the work outright (slice 2).
6. ``changes_requested``/``review_failed``/``code_reviewed`` — derived from
   ``code_review_status``. Listed above ``needs_rework`` so that a fresh
   code-review verdict on a reworked item supersedes the stale operator
   change request.
7. ``preview_ready``    — preview has been deployed. Overrides a prior
   operator change request: a new preview deploy means the builder
   addressed the change request and the work is back in review.
8. ``preview_pending``  — preview is required but not yet deployed.
9. ``needs_rework``     — operator sent the work back with a change request
   (slice 2). Only applies when no review-ready signal above (code-review
   verdict, preview deploy) has arrived since the change request.
10. ``in_review``       — a PR exists (number or URL) but no terminal review.
    A bare PR is *not* a review-ready signal and does not override
    ``needs_rework`` — otherwise the very PR that triggered the change
    request would silently clear it.
11. ``blocked``         — Kanban ``status`` is ``blocked``.
12. ``building``        — Kanban ``status`` indicates implementation in flight.
13. ``implemented``     — Kanban ``status`` indicates completion.
14. ``review_needed``   — Kanban ``status`` is review/review_needed.
15. ``approved``        — operator has approved but no builder activity yet.
16. ``debating``        — a debate run is currently active.
17. ``debated``         — a debate has completed and no later state applies.
18. ``drafting``        — fallback for fresh/draft items.
19. Otherwise: the raw Kanban status (lowercased) is returned so we never
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

    # 2. Complete: operator has acknowledged a verified post-merge deploy
    # (slice 5). Terminal positive outcome — outranks ``merged`` because
    # ``complete`` items still carry the merge SHA.
    if getattr(work_item, "completed_at", None) is not None:
        return "complete"

    # 3/4. Merge SHA terminal states (slice 5).
    merge_sha = _norm(getattr(work_item, "merge_commit_sha", None))
    if merge_sha:
        # Post-merge healthcheck explicitly failed — operator must fix
        # before the item can be marked ``complete``.
        health = _norm(getattr(work_item, "post_merge_health_status", None))
        if health in ("unhealthy", "failed", "error"):
            return "merged_deployment_failed"
        return "merged"

    # 5. Merge attempt failed before any SHA was recorded — surface a
    # distinct state so the UI can prompt the operator to retry.
    merge_status = _norm(getattr(work_item, "merge_status", None))
    if merge_status in ("failed", "blocked"):
        return "blocked_merge"

    # 6. Ready-to-merge gate (set by slice 5 once everything else passes).
    if getattr(work_item, "ready_to_merge", None) is True:
        return "ready_to_merge"

    # 7. Operator certification (slice 2).
    if getattr(work_item, "operator_certified", None) is True:
        return "certified"

    # 5. Operator rejection (slice 2) — operator abandoned the work outright.
    if getattr(work_item, "rejected_at", None) is not None:
        return "rejected"

    # 6. Code review verdict, when present, supersedes both the generic PR
    # state and any earlier operator change request. An approved (or failed,
    # or automated changes_requested) review after a rework cycle means the
    # builder addressed the operator's notes and the work is back in review.
    code_review = _norm(getattr(work_item, "code_review_status", None))
    if code_review == "approved":
        return "code_reviewed"
    if code_review in ("changes_requested", "changes-requested"):
        return "changes_requested"
    if code_review == "failed":
        return "review_failed"

    # 7/8. Preview flags (slice 4 surfaces these). A new preview deploy after
    # a rework cycle is a review-ready signal and must override any earlier
    # operator change request — otherwise the lifecycle would stay pinned in
    # ``needs_rework`` forever.
    preview_required = getattr(work_item, "preview_required", None) is True
    preview_deployed = getattr(work_item, "preview_deployed", None) is True
    if preview_deployed:
        return "preview_ready"
    if preview_required:
        return "preview_pending"

    # 9. Operator change request (slice 2) — operator sent the work back for
    # rework. Only applies when no later review-ready signal (preview deploy
    # or code review verdict above) has arrived. Distinct from the
    # ``changes_requested`` verdict derived from ``code_review_status``
    # (an automated/agent verdict).
    if getattr(work_item, "changes_requested_at", None) is not None:
        return "needs_rework"

    # 10. PR exists but no review/preview verdict yet. A bare PR is NOT a
    # review-ready signal — if it were, this branch would let an operator
    # change request be silently overridden by the PR that triggered it.
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
