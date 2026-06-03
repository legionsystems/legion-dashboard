"""Tests for implementation Kanban card prompt assembly.

Covers the WI-17-style failure mode where the generated card lost or
contradicted the debate's mandatory edits, and the readiness rewrite
that turned ``READY_AFTER_EDITS`` into ``READY_NOW``.

These tests exercise the canonical prompt assembly in
:mod:`app.builder_card` and the ``_generate_hermes_prompt`` adapter in
:mod:`app.routers.builder`.
"""
from __future__ import annotations

import json
from typing import Optional

import pytest

from app import builder_card
from app.builder_card import (
    DEFAULT_OUT_OF_SCOPE_ITEMS,
    build_implementation_card_prompt,
    check_out_of_scope_contradictions,
    load_arbiter_mandatory_edits,
    render_mandatory_edits_section,
    render_prompt_assembly_warnings,
)
from app.models import DebateArgument, DebateRun, WorkItem
from app.routers import builder as builder_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_work_item(**overrides) -> WorkItem:
    item = WorkItem(
        type="task",
        title="Implement AUTO_ASSIGN stance classification",
        body="Add deterministic operator-argument stance auto-assignment.",
        status="approved",
        priority="medium",
        source="operator",
        approved_by_operator=True,
        acceptance_notes=(
            "Operators must see a deterministic PRO/CON/NEUTRAL assignment on "
            "every operator-argument. Stance must be persisted with the debate "
            "run and surfaced in the UI."
        ),
    )
    for k, v in overrides.items():
        setattr(item, k, v)
    return item


def _seed_debate_run(
    db_session,
    *,
    recommendation: str = "APPROVE_WITH_MANDATORY_EDITS",
    readiness: str = "READY_AFTER_EDITS",
    arbiter_json: Optional[dict] = None,
    work_item_id: int = 1,
) -> DebateRun:
    """Seed a completed debate run with an arbiter argument carrying JSON.

    The structured arbiter output is stored on the latest ``DebateArgument``
    with ``side == 'arbiter'``. The DebateRun's ``summary`` field stores
    the rationale text, not the JSON — that distinction is the whole
    reason this fix exists.
    """
    run = DebateRun(
        work_item_id=work_item_id,
        work_item_type_snapshot="task",
        status="completed",
        final_recommendation=recommendation,
        implementation_readiness=readiness,
        summary="The debate approved the work item with concrete mandatory edits.",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    arbiter_content = json.dumps(
        arbiter_json
        if arbiter_json is not None
        else {
            "recommendation": recommendation,
            "implementation_readiness": readiness,
            "mandatory_edits": [
                {
                    "field": "operator_argument_stance",
                    "current_problem": (
                        "Operator arguments are stored without a deterministic "
                        "PRO/CON/NEUTRAL assignment."
                    ),
                    "required_change": (
                        "Add deterministic AUTO_ASSIGN classification into "
                        "PRO/CON/NEUTRAL and persist the calculated stance "
                        "with the debate run."
                    ),
                }
            ],
        }
    )
    arbiter_arg = DebateArgument(
        debate_run_id=run.id,
        round_number=3,
        role="Final Arbiter",
        side="arbiter",
        content=arbiter_content,
        claim_id="R3-arbiter-FA-001",
    )
    db_session.add(arbiter_arg)
    db_session.commit()
    db_session.refresh(arbiter_arg)
    return run


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def test_render_mandatory_edits_section_includes_known_fields():
    edits = [
        {
            "field": "operator_argument_stance",
            "current_problem": "No deterministic assignment is computed.",
            "required_change": (
                "Compute AUTO_ASSIGN PRO/CON/NEUTRAL and persist it on the "
                "debate run."
            ),
        }
    ]
    out = render_mandatory_edits_section(edits)

    assert "MANDATORY EDITS FROM DEBATE" in out
    assert "field: operator_argument_stance" in out
    assert "current_problem: No deterministic assignment is computed." in out
    assert "required_change:" in out
    assert "AUTO_ASSIGN PRO/CON/NEUTRAL" in out


def test_render_mandatory_edits_section_marks_missing_fields_as_not_provided():
    edits = [{"field": "only_field"}]
    out = render_mandatory_edits_section(edits)

    assert "field: only_field" in out
    assert "current_problem: (not provided)" in out
    assert "required_change: (not provided)" in out


def test_render_mandatory_edits_section_empty_returns_empty_string():
    assert render_mandatory_edits_section([]) == ""


# ---------------------------------------------------------------------------
# load_arbiter_mandatory_edits
# ---------------------------------------------------------------------------


def test_load_arbiter_mandatory_edits_reads_arbiter_argument_json(
    client, db_session
):
    item = _make_work_item()
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    run = _seed_debate_run(db_session, work_item_id=item.id)

    edits = load_arbiter_mandatory_edits(db_session, run)

    assert len(edits) == 1
    assert edits[0]["field"] == "operator_argument_stance"
    assert "AUTO_ASSIGN" in edits[0]["required_change"]


def test_load_arbiter_mandatory_edits_handles_code_fenced_json(
    client, db_session
):
    item = _make_work_item()
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    run = DebateRun(
        work_item_id=item.id,
        work_item_type_snapshot="task",
        status="completed",
        final_recommendation="APPROVE_WITH_MANDATORY_EDITS",
        implementation_readiness="READY_AFTER_EDITS",
        summary="x",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    payload = {
        "recommendation": "APPROVE_WITH_MANDATORY_EDITS",
        "implementation_readiness": "READY_AFTER_EDITS",
        "mandatory_edits": [
            {
                "field": "f1",
                "current_problem": "p1",
                "required_change": "c1",
            }
        ],
    }
    arg = DebateArgument(
        debate_run_id=run.id,
        round_number=3,
        role="Final Arbiter",
        side="arbiter",
        content="```json\n" + json.dumps(payload) + "\n```",
    )
    db_session.add(arg)
    db_session.commit()

    edits = load_arbiter_mandatory_edits(db_session, run)
    assert len(edits) == 1
    assert edits[0]["field"] == "f1"


def test_load_arbiter_mandatory_edits_returns_empty_when_no_arbiter_arg(
    client, db_session
):
    item = _make_work_item()
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    run = DebateRun(
        work_item_id=item.id,
        work_item_type_snapshot="task",
        status="completed",
        final_recommendation="APPROVE_AS_IS",
        implementation_readiness="READY_NOW",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    assert load_arbiter_mandatory_edits(db_session, run) == []


# ---------------------------------------------------------------------------
# Contradiction detection
# ---------------------------------------------------------------------------


def test_contradiction_check_flags_wi17_out_of_scope_conflict():
    """The WI-17 failure: task says implement AUTO_ASSIGN, but the
    template out-of-scope list says 'operator-argument stance auto-assign
    is out of scope'. The check must catch that."""
    item = _make_work_item()
    edits = [
        {
            "field": "operator_argument_stance",
            "current_problem": "x",
            "required_change": (
                "Implement deterministic AUTO_ASSIGN classification into "
                "PRO/CON/NEUTRAL."
            ),
        }
    ]
    result = check_out_of_scope_contradictions(item, edits, DEFAULT_OUT_OF_SCOPE_ITEMS)
    removed = [r["removed_item"] for r in result.removed_items]
    assert any("operator-argument stance auto-assign" in r for r in removed), (
        f"WI-17 out-of-scope item must be removed, got: {removed!r}"
    )
    assert result.filtered_out_of_scope == [
        "Do not use direct GitHub API orchestration.",
        "Do not create external/public demo or staging deployments unless already part of the existing Hermes implementation flow.",
    ]


def test_contradiction_check_keeps_non_conflicting_out_of_scope():
    # Use a work item with no overlap to either mandatory edits or the
    # body of the item. Body must also be overridden because the helper
    # default carries the WI-17 phrasing.
    item = _make_work_item(
        title="Reorganize dashboard navigation",
        body="Move the Work Items nav entry to the top of the sidebar.",
        acceptance_notes="No acceptance criteria changes; this is nav-only.",
    )
    # No mandatory edits; nothing should be removed.
    result = check_out_of_scope_contradictions(
        item, [], DEFAULT_OUT_OF_SCOPE_ITEMS
    )
    assert result.removed_items == []
    assert len(result.filtered_out_of_scope) == len(DEFAULT_OUT_OF_SCOPE_ITEMS)


def test_contradiction_check_flags_acceptance_notes_overlap():
    item = _make_work_item()
    item.acceptance_notes = "Must not use direct GitHub API orchestration."
    result = check_out_of_scope_contradictions(item, [], DEFAULT_OUT_OF_SCOPE_ITEMS)
    removed = [r["removed_item"] for r in result.removed_items]
    assert "Do not use direct GitHub API orchestration." in removed


def test_render_prompt_assembly_warnings_includes_removed_and_reason():
    block = render_prompt_assembly_warnings(
        [
            {
                "removed_item": "Do not implement X",
                "reason": "contradicts mandatory edit",
                "matched_phrase": "implement X",
            }
        ]
    )
    assert "PROMPT ASSEMBLY WARNING" in block
    assert "Removed item: Do not implement X" in block
    assert "Reason: contradicts mandatory edit" in block


# ---------------------------------------------------------------------------
# build_implementation_card_prompt
# ---------------------------------------------------------------------------


def test_build_card_approve_with_mandatory_edits_renders_canonical_section():
    item = _make_work_item()
    body = build_implementation_card_prompt(
        item,
        debate_run_id=42,
        recommendation="APPROVE_WITH_MANDATORY_EDITS",
        implementation_readiness="READY_AFTER_EDITS",
        mandatory_edits=[
            {
                "field": "operator_argument_stance",
                "current_problem": "No deterministic assignment is computed.",
                "required_change": "Implement deterministic AUTO_ASSIGN.",
            }
        ],
        target_repo="/srv/repo/legion-dashboard",
    )

    # Mandatory edits block is present, canonical, structured.
    assert "MANDATORY EDITS FROM DEBATE" in body
    assert "field: operator_argument_stance" in body
    assert "current_problem: No deterministic assignment is computed." in body
    assert "required_change: Implement deterministic AUTO_ASSIGN." in body

    # Debate outcome is rendered as-is (not rewritten).
    assert "Recommendation: APPROVE_WITH_MANDATORY_EDITS" in body
    assert "Implementation Readiness: READY_AFTER_EDITS" in body
    assert "Debate Run ID: 42" in body

    # Builder-side enforcement is present for the WI-17 directive.
    assert "BUILDER DIRECTIVE — MANDATORY EDITS" in body
    assert "Do not skip, downgrade, rename, omit" in body
    assert "Do not mark this task complete" in body


def test_build_card_does_not_rewrite_ready_after_edits_to_ready_now():
    item = _make_work_item()
    body = build_implementation_card_prompt(
        item,
        debate_run_id=99,
        recommendation="APPROVE_WITH_MANDATORY_EDITS",
        implementation_readiness="READY_AFTER_EDITS",
        mandatory_edits=[
            {
                "field": "f",
                "current_problem": "p",
                "required_change": "c",
            }
        ],
    )
    assert "Implementation Readiness: READY_AFTER_EDITS" in body
    # No fabricated READY_NOW rendering for an APPROVE_WITH_MANDATORY_EDITS run.
    assert "Implementation Readiness: READY_NOW" not in body


def test_build_card_approve_as_is_unchanged_no_directive():
    item = _make_work_item()
    body = build_implementation_card_prompt(
        item,
        debate_run_id=99,
        recommendation="APPROVE_AS_IS",
        implementation_readiness="READY_NOW",
        mandatory_edits=[],
    )
    assert "MANDATORY EDITS FROM DEBATE" not in body
    assert "BUILDER DIRECTIVE — MANDATORY EDITS" not in body
    assert "Recommendation: APPROVE_AS_IS" in body
    assert "Implementation Readiness: READY_NOW" in body


def test_build_card_wi17_contradiction_removed_and_warning_emitted():
    item = _make_work_item()
    body = build_implementation_card_prompt(
        item,
        debate_run_id=42,
        recommendation="APPROVE_WITH_MANDATORY_EDITS",
        implementation_readiness="READY_AFTER_EDITS",
        mandatory_edits=[
            {
                "field": "operator_argument_stance",
                "current_problem": (
                    "Operator arguments have no deterministic stance."
                ),
                "required_change": (
                    "Implement deterministic AUTO_ASSIGN classification into "
                    "PRO/CON/NEUTRAL."
                ),
            }
        ],
    )
    # The contradictory out-of-scope line must be removed from the
    # OUT OF SCOPE section, but it MUST still appear inside the
    # PROMPT ASSEMBLY WARNING block so the operator can see what was
    # removed and why.
    out_of_scope_block = body.split("OUT OF SCOPE", 1)[1].split(
        "PROMPT ASSEMBLY WARNING", 1
    )[0]
    assert "operator-argument stance auto-assign" not in out_of_scope_block
    # The surviving out-of-scope lines must still be present.
    assert "Do not use direct GitHub API orchestration." in body
    # The PROMPT ASSEMBLY WARNING must surface the removal.
    assert "PROMPT ASSEMBLY WARNING" in body
    assert "Removed item:" in body
    assert "operator-argument stance auto-assign" in body  # shown in the warning


def test_build_card_no_debate_renders_safely():
    item = _make_work_item()
    body = build_implementation_card_prompt(
        item,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[],
    )
    assert "Recommendation: Not recorded" in body
    assert "Implementation Readiness: Not recorded" in body
    assert "Debate Run ID: Not recorded" in body
    # No spurious mandatory edits block.
    assert "MANDATORY EDITS FROM DEBATE" not in body


# ---------------------------------------------------------------------------
# Integration with router adapter
# ---------------------------------------------------------------------------


def test_router_adapter_pulls_mandatory_edits_from_arbiter_argument(
    client, db_session, monkeypatch
):
    """End-to-end: a real DebateRun + arbiter argument with the WI-17
    JSON drives the prompt body the builder would receive."""
    item = _make_work_item()
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    run = _seed_debate_run(db_session, work_item_id=item.id)

    body = builder_router._generate_hermes_prompt(
        work_item=item,
        db=db_session,
        debate_run_id=run.id,
        recommendation=run.final_recommendation,
        implementation_readiness=run.implementation_readiness,
    )

    assert "MANDATORY EDITS FROM DEBATE" in body
    assert "field: operator_argument_stance" in body
    assert "Recommendation: APPROVE_WITH_MANDATORY_EDITS" in body
    assert "Implementation Readiness: READY_AFTER_EDITS" in body
    # The contradictory out-of-scope line was auto-removed from the
    # OUT OF SCOPE list. It still appears inside PROMPT ASSEMBLY WARNING
    # as a removed item, which is the intended behaviour.
    out_of_scope_block = body.split("OUT OF SCOPE", 1)[1].split(
        "PROMPT ASSEMBLY WARNING", 1
    )[0]
    assert "operator-argument stance auto-assign" not in out_of_scope_block
    # And a warning was emitted to make the auto-removal visible.
    assert "PROMPT ASSEMBLY WARNING" in body


def test_router_adapter_no_debate_uses_explicit_edits_only():
    item = _make_work_item()
    body = builder_router._generate_hermes_prompt(
        work_item=item,
        debate_run_id=None,
        recommendation=None,
        implementation_readiness=None,
        mandatory_edits=[
            {
                "field": "f",
                "current_problem": "p",
                "required_change": "c",
            }
        ],
    )
    assert "MANDATORY EDITS FROM DEBATE" in body
    assert "field: f" in body


def test_router_adapter_does_not_treat_rationale_summary_as_mandatory_edits(
    client, db_session, monkeypatch
):
    """Regression: DebateRun.summary is the rationale TEXT, not JSON.
    Passing it through as ``mandatory_edits_json`` (the old bug) must
    not produce spurious mandatory-edits bullets."""
    item = _make_work_item()
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    run = _seed_debate_run(db_session, work_item_id=item.id)
    # Summary is rationale text, not JSON — adapter must ignore it
    # when computing the prompt.
    assert run.summary is not None
    assert not run.summary.strip().startswith("[")

    body = builder_router._generate_hermes_prompt(
        work_item=item,
        db=db_session,
        debate_run_id=run.id,
        recommendation=run.final_recommendation,
        implementation_readiness=run.implementation_readiness,
    )

    # The mandatory edits are the structured ones, not the rationale.
    assert "MANDATORY EDITS FROM DEBATE" in body
    assert "field: operator_argument_stance" in body
    # Rationale text does NOT leak into the mandatory edits block.
    edits_block = body.split("MANDATORY EDITS FROM DEBATE", 1)[1].split(
        "OUT OF SCOPE", 1
    )[0]
    assert "concrete mandatory edits" not in edits_block
