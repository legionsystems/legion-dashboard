"""Tests for ``app.lifecycle.compute_effective_state``.

These are pure-Python unit tests against the projection function. Instead of
pulling in the SQLAlchemy ``WorkItem`` model (which would require a DB
fixture for every case), we drive the function with a lightweight
``SimpleNamespace`` carrying the same attribute names.

Each test asserts a single precedence rule so a regression makes the failure
obvious.
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.lifecycle import compute_effective_state


# Default attribute set so individual tests only need to override what they
# care about. Mirrors a freshly-created draft work item.
_DEFAULTS = dict(
    archived=False,
    status="draft",
    approved_by_operator=False,
    pr_url=None,
    pr_number=None,
    merge_commit_sha=None,
    code_review_status=None,
    preview_required=None,
    preview_deployed=None,
    operator_certified=None,
    ready_to_merge=None,
)


def _wi(**overrides):
    data = {**_DEFAULTS, **overrides}
    return SimpleNamespace(**data)


def _debate(status: str):
    return SimpleNamespace(status=status)


# ---------------------------------------------------------------------------
# Precedence: archived and merged dominate everything below them.
# ---------------------------------------------------------------------------


def test_archived_wins_over_everything():
    item = _wi(
        archived=True,
        merge_commit_sha="deadbeef",
        operator_certified=True,
        ready_to_merge=True,
    )
    assert compute_effective_state(item) == "archived"


def test_merged_wins_over_certified_and_ready_to_merge():
    item = _wi(
        merge_commit_sha="abc123",
        ready_to_merge=True,
        operator_certified=True,
    )
    assert compute_effective_state(item) == "merged"


def test_empty_merge_sha_is_not_merged():
    # Whitespace-only / empty strings must not promote to merged.
    item = _wi(merge_commit_sha="   ", approved_by_operator=True)
    assert compute_effective_state(item) == "approved"


# ---------------------------------------------------------------------------
# Operator gates: ready_to_merge → certified.
# ---------------------------------------------------------------------------


def test_ready_to_merge_outranks_certified():
    item = _wi(ready_to_merge=True, operator_certified=True)
    assert compute_effective_state(item) == "ready_to_merge"


def test_certified_outranks_code_review_approved():
    item = _wi(operator_certified=True, code_review_status="approved")
    assert compute_effective_state(item) == "certified"


# ---------------------------------------------------------------------------
# Operator change request (needs_rework) — slice 2.
#
# A stale operator change request must not permanently pin the lifecycle:
# later review-ready signals (preview_deployed, code_review_status) must
# override ``needs_rework`` so the operator can certify after the builder
# addresses the change request.
# ---------------------------------------------------------------------------


_CHANGE_REQUEST_AT = datetime(2026, 6, 1, 12, 0, 0)


def test_needs_rework_when_only_operator_change_request_set():
    item = _wi(
        changes_requested_at=_CHANGE_REQUEST_AT,
        change_request="Please add input validation.",
    )
    assert compute_effective_state(item) == "needs_rework"


def test_bare_pr_does_not_override_operator_change_request():
    # A bare PR (no preview signal, no code-review verdict) is not a
    # review-ready signal — the operator's change request still wins.
    item = _wi(
        pr_number=42,
        changes_requested_at=_CHANGE_REQUEST_AT,
        change_request="Please add input validation.",
    )
    assert compute_effective_state(item) == "needs_rework"


def test_preview_deployed_overrides_stale_change_request():
    item = _wi(
        pr_number=42,
        changes_requested_at=_CHANGE_REQUEST_AT,
        change_request="Please add input validation.",
        preview_required=True,
        preview_deployed=True,
    )
    assert compute_effective_state(item) == "preview_ready"


def test_preview_pending_overrides_stale_change_request():
    # When preview is required but not yet deployed, the builder is
    # actively working — surface ``preview_pending`` so the operator
    # knows what they are waiting on rather than the stale rework state.
    item = _wi(
        pr_number=42,
        changes_requested_at=_CHANGE_REQUEST_AT,
        change_request="Please add input validation.",
        preview_required=True,
        preview_deployed=False,
    )
    assert compute_effective_state(item) == "preview_pending"


def test_code_review_approved_overrides_stale_change_request():
    item = _wi(
        pr_number=42,
        changes_requested_at=_CHANGE_REQUEST_AT,
        change_request="Please add input validation.",
        code_review_status="approved",
    )
    assert compute_effective_state(item) == "code_reviewed"


def test_code_review_changes_requested_overrides_operator_change_request():
    # Automated/agent ``changes_requested`` verdict is distinct from the
    # operator's change request and takes precedence over a stale one.
    item = _wi(
        pr_number=42,
        changes_requested_at=_CHANGE_REQUEST_AT,
        change_request="Please add input validation.",
        code_review_status="changes_requested",
    )
    assert compute_effective_state(item) == "changes_requested"


def test_rejected_still_wins_over_change_request_and_preview():
    # ``rejected`` is terminal — even if a preview is deployed later,
    # rejection is still the right state.
    item = _wi(
        pr_number=42,
        changes_requested_at=_CHANGE_REQUEST_AT,
        rejected_at=datetime(2026, 6, 2, 12, 0, 0),
        preview_deployed=True,
    )
    assert compute_effective_state(item) == "rejected"


# ---------------------------------------------------------------------------
# Code review verdicts.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "review_status, expected",
    [
        ("approved", "code_reviewed"),
        ("changes_requested", "changes_requested"),
        ("changes-requested", "changes_requested"),
        ("failed", "review_failed"),
    ],
)
def test_code_review_status_projects(review_status, expected):
    item = _wi(pr_number=42, code_review_status=review_status)
    assert compute_effective_state(item) == expected


def test_code_review_pending_falls_through_to_in_review():
    # A "pending" review verdict has no dedicated state — having a PR is
    # enough to land on ``in_review``.
    item = _wi(pr_number=7, code_review_status="pending")
    assert compute_effective_state(item) == "in_review"


# ---------------------------------------------------------------------------
# Preview flags.
# ---------------------------------------------------------------------------


def test_preview_deployed_wins_over_preview_required():
    item = _wi(preview_required=True, preview_deployed=True, pr_number=1)
    assert compute_effective_state(item) == "preview_ready"


def test_preview_required_without_deploy():
    item = _wi(preview_required=True, pr_number=1)
    assert compute_effective_state(item) == "preview_pending"


def test_preview_flags_none_do_not_promote():
    # Explicit None must behave like "unset" — never trigger preview states.
    item = _wi(preview_required=None, preview_deployed=None, pr_number=1)
    assert compute_effective_state(item) == "in_review"


# ---------------------------------------------------------------------------
# PR presence drives ``in_review`` when no later signal is set.
# ---------------------------------------------------------------------------


def test_pr_number_drives_in_review():
    item = _wi(pr_number=99)
    assert compute_effective_state(item) == "in_review"


def test_pr_url_drives_in_review_without_number():
    item = _wi(pr_url="https://github.com/x/y/pull/3")
    assert compute_effective_state(item) == "in_review"


# ---------------------------------------------------------------------------
# Kanban status fallbacks.
# ---------------------------------------------------------------------------


def test_blocked_status():
    assert compute_effective_state(_wi(status="blocked")) == "blocked"


@pytest.mark.parametrize("status", ["active", "building", "in_progress"])
def test_building_statuses(status):
    assert compute_effective_state(_wi(status=status)) == "building"


@pytest.mark.parametrize("status", ["completed", "implemented", "done"])
def test_implemented_statuses(status):
    assert compute_effective_state(_wi(status=status)) == "implemented"


@pytest.mark.parametrize("status", ["review", "review_needed"])
def test_review_statuses(status):
    assert compute_effective_state(_wi(status=status)) == "review_needed"


def test_approved_when_no_build_activity():
    item = _wi(approved_by_operator=True, status="approved")
    assert compute_effective_state(item) == "approved"


def test_building_overrides_approved():
    # An approved item that the builder has picked up should report
    # ``building``, not ``approved``.
    item = _wi(approved_by_operator=True, status="active")
    assert compute_effective_state(item) == "building"


# ---------------------------------------------------------------------------
# Debate fallback states.
# ---------------------------------------------------------------------------


def test_active_debate_marks_debating():
    item = _wi(status="draft")
    assert (
        compute_effective_state(item, _debate("running"))
        == "debating"
    )


def test_terminal_debate_marks_debated():
    item = _wi(status="draft")
    assert (
        compute_effective_state(item, _debate("completed"))
        == "debated"
    )


def test_debate_does_not_override_pr():
    item = _wi(status="draft", pr_number=5)
    # Even with an active debate, an existing PR is a stronger signal.
    assert (
        compute_effective_state(item, _debate("running"))
        == "in_review"
    )


# ---------------------------------------------------------------------------
# Draft + unknown fallbacks.
# ---------------------------------------------------------------------------


def test_drafting_default():
    assert compute_effective_state(_wi(status="draft")) == "drafting"


def test_none_status_falls_through_to_drafting():
    assert compute_effective_state(_wi(status=None)) == "drafting"


def test_unknown_status_is_returned_verbatim():
    # An unrecognized status should be surfaced lowercased — never
    # silently mapped to ``drafting``.
    assert (
        compute_effective_state(_wi(status="PENDING_APPROVAL"))
        == "pending_approval"
    )
