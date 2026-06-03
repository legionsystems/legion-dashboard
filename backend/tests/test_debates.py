"""Tests for the debate workflow.

Covers:
- Debate auto-attaches to every work-item type when the item is created in a
  debate-eligible status.
- Rounds are clamped at the schema layer (rejected as 422).
- Manual rerun creates a *new* DebateRun and never mutates prior runs.
- Operator arguments persist independently and can be attached to runs.
- Debate execution does NOT auto-approve or auto-start implementation.
- ``latest_debate`` is surfaced on the work-item list response.
"""

import pytest

DEBATE_TYPES = ("idea", "bug", "change", "task", "slice")


def _create(client, **overrides):
    payload = {"type": "task", "title": "Sample", "body": "Body"}
    payload.update(overrides)
    response = client.post("/api/work-items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _debate_list(client, item_id):
    response = client.get(f"/api/work-items/{item_id}/debates")
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Auto-attach per type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item_type", DEBATE_TYPES)
def test_debate_auto_attaches_to_each_type_in_debate_eligible_status(
    client, item_type
):
    item = _create(client, type=item_type, title=f"{item_type} item")
    # Items are created in status='draft', which IS debate-eligible.
    runs = _debate_list(client, item["id"])
    assert len(runs) == 1, (
        f"expected an automatic debate run for {item_type}, got {runs}"
    )
    run = runs[0]
    assert run["status"] == "queued"
    assert run["trigger"] == "automatic"
    assert run["work_item_type_snapshot"] == item_type
    assert run["final_recommendation"] is None  # advisory — not yet finalized
    assert run["rounds_requested"] == 2


def test_debate_does_not_auto_queue_for_non_eligible_status(client):
    item = _create(client, title="Already active", status="active")
    runs = _debate_list(client, item["id"])
    assert runs == []


def test_debate_queues_on_status_transition_into_eligible(client):
    item = _create(client, title="Started active", status="active")
    assert _debate_list(client, item["id"]) == []
    response = client.put(
        f"/api/work-items/{item['id']}",
        json={"status": "review_needed"},
    )
    assert response.status_code == 200
    runs = _debate_list(client, item["id"])
    assert len(runs) == 1
    assert runs[0]["status"] == "queued"


def test_no_duplicate_run_when_snapshot_unchanged(client):
    """Editing a debate-eligible item without changing any snapshot field
    must not stack identical runs."""
    item = _create(client, title="Same", status="draft")
    first = _debate_list(client, item["id"])
    assert len(first) == 1
    # Update with an unrelated field (pr_url isn't in SNAPSHOT_FIELDS).
    response = client.put(
        f"/api/work-items/{item['id']}",
        json={"pr_url": "https://example.com/pr/1"},
    )
    assert response.status_code == 200
    second = _debate_list(client, item["id"])
    assert len(second) == 1, "must not create a duplicate run for an unchanged snapshot"


def test_changing_title_queues_a_new_run(client):
    item = _create(client, title="Original", status="draft")
    assert len(_debate_list(client, item["id"])) == 1
    response = client.put(
        f"/api/work-items/{item['id']}",
        json={"title": "Edited title — new debate-relevant content"},
    )
    assert response.status_code == 200
    runs = _debate_list(client, item["id"])
    assert len(runs) == 2


# ---------------------------------------------------------------------------
# Rounds clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rounds", [0, -1, 6, 99])
def test_rounds_outside_range_rejected(client, rounds):
    item = _create(client, title="Round-test")
    response = client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": rounds, "trigger": "manual_rerun"},
    )
    assert response.status_code == 422, response.text


@pytest.mark.parametrize("rounds", [1, 2, 3, 4, 5])
def test_rounds_inside_range_accepted(client, rounds):
    item = _create(client, title="Round-test")
    response = client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": rounds, "trigger": "manual_rerun"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["rounds_requested"] == rounds


# ---------------------------------------------------------------------------
# Manual rerun
# ---------------------------------------------------------------------------


def test_manual_rerun_creates_new_run_and_preserves_old(client):
    item = _create(client, title="Rerun me")
    initial = _debate_list(client, item["id"])
    assert len(initial) == 1
    original_id = initial[0]["id"]

    response = client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 3, "trigger": "manual_rerun"},
    )
    assert response.status_code == 201
    new_run = response.json()
    assert new_run["id"] != original_id
    assert new_run["trigger"] == "manual_rerun"
    assert new_run["rounds_requested"] == 3

    runs = _debate_list(client, item["id"])
    assert len(runs) == 2
    # Older run must be untouched.
    older = next(r for r in runs if r["id"] == original_id)
    assert older["status"] == "queued"
    assert older["trigger"] == "automatic"


def test_manual_rerun_works_when_snapshot_unchanged(client):
    """Operator-driven reruns must bypass snapshot de-duplication."""
    item = _create(client, title="No edits")
    runs = _debate_list(client, item["id"])
    assert len(runs) == 1

    response = client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 2, "trigger": "manual_rerun"},
    )
    assert response.status_code == 201
    assert len(_debate_list(client, item["id"])) == 2


def test_get_single_debate_run_includes_arguments(client):
    item = _create(client, title="Has arg")
    runs = _debate_list(client, item["id"])
    run_id = runs[0]["id"]
    response = client.get(
        f"/api/work-items/{item['id']}/debates/{run_id}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert isinstance(body["arguments"], list)
    # In the unconfigured-bridge default, we record a single System note.
    assert len(body["arguments"]) >= 1
    note = body["arguments"][0]
    assert note["role"] == "System"
    assert "disabled" in note["content"].lower() or "execution" in note["content"].lower()


# ---------------------------------------------------------------------------
# Operator arguments
# ---------------------------------------------------------------------------


def test_operator_argument_persists(client):
    item = _create(client, title="Operator-arg")
    response = client.post(
        f"/api/work-items/{item['id']}/debate-inputs",
        json={"content": "I have concerns about scope.", "stance_requested": "con"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["content"] == "I have concerns about scope."
    assert body["stance_requested"] == "con"
    assert body["considered_in_run_id"] is None

    listed = client.get(f"/api/work-items/{item['id']}/debate-inputs")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_operator_argument_rejects_empty_content(client):
    item = _create(client, title="Empty-arg")
    response = client.post(
        f"/api/work-items/{item['id']}/debate-inputs",
        json={"content": "   ", "stance_requested": "neutral"},
    )
    assert response.status_code == 422


def test_operator_argument_rejects_invalid_stance(client):
    item = _create(client, title="Bad-stance")
    response = client.post(
        f"/api/work-items/{item['id']}/debate-inputs",
        json={"content": "Real content.", "stance_requested": "wibble"},
    )
    assert response.status_code == 422


def test_operator_argument_attached_to_manual_rerun(client):
    item = _create(client, title="Attach-test")
    op_arg = client.post(
        f"/api/work-items/{item['id']}/debate-inputs",
        json={
            "content": "Architectural concern: data ownership.",
            "stance_requested": "auto_assign",
        },
    )
    assert op_arg.status_code == 201
    op_arg_id = op_arg.json()["id"]

    rerun = client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 2, "trigger": "manual_rerun"},
    )
    assert rerun.status_code == 201
    run_id = rerun.json()["id"]

    # The operator input should now be linked to the rerun.
    inputs = client.get(f"/api/work-items/{item['id']}/debate-inputs").json()
    linked = next(i for i in inputs if i["id"] == op_arg_id)
    assert linked["considered_in_run_id"] == run_id

    # And visible in the run's argument list under Operator role.
    full = client.get(
        f"/api/work-items/{item['id']}/debates/{run_id}"
    ).json()
    op_args = [a for a in full["arguments"] if a["role"] == "Operator"]
    assert len(op_args) == 1
    assert op_args[0]["content"] == "Architectural concern: data ownership."


def test_auto_assign_stance_recorded_when_run_consumes_input(client):
    """An auto_assign input keeps stance_assigned=None until execution
    actually places it on a side. With the bridge unconfigured we leave
    stance_assigned NULL on purpose — see debate.attach_operator_inputs_to_run.
    """
    item = _create(client, title="Stance-record")
    client.post(
        f"/api/work-items/{item['id']}/debate-inputs",
        json={"content": "Mild concern.", "stance_requested": "pro"},
    )
    rerun = client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 2, "trigger": "manual_rerun"},
    )
    assert rerun.status_code == 201
    inputs = client.get(f"/api/work-items/{item['id']}/debate-inputs").json()
    assert inputs[0]["stance_assigned"] == "pro"
    assert inputs[0]["considered_in_run_id"] == rerun.json()["id"]


# ---------------------------------------------------------------------------
# Safety: advisory only
# ---------------------------------------------------------------------------


def test_debate_does_not_auto_approve(client):
    item = _create(client, title="Stay un-approved")
    # Trigger an additional manual rerun for good measure.
    client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 2, "trigger": "manual_rerun"},
    )
    refreshed = client.get(f"/api/work-items/{item['id']}").json()
    assert refreshed["approved_by_operator"] is False
    assert refreshed["approval_timestamp"] is None


def test_debate_does_not_set_pr_or_merge(client):
    item = _create(client, title="No-pr-after-debate")
    client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 2, "trigger": "manual_rerun"},
    )
    refreshed = client.get(f"/api/work-items/{item['id']}").json()
    assert refreshed["pr_url"] is None
    assert refreshed["merge_commit_sha"] is None


def test_invalid_trigger_rejected(client):
    item = _create(client, title="Bad-trigger")
    response = client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 2, "trigger": "automatic"},  # automatic not allowed via API
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# List-page integration
# ---------------------------------------------------------------------------


def test_latest_debate_surfaced_on_work_item_response(client):
    item = _create(client, title="Has-latest", type="bug")
    # GET single returns latest_debate populated.
    detail = client.get(f"/api/work-items/{item['id']}").json()
    assert detail["latest_debate"] is not None
    assert detail["latest_debate"]["status"] == "queued"
    assert detail["latest_debate"]["work_item_type_snapshot"] == "bug"


def test_latest_debate_in_list_response(client):
    item_a = _create(client, title="A", type="idea")
    item_b = _create(client, title="B", status="active")  # not debate-eligible
    listing = client.get("/api/work-items").json()
    a = next(x for x in listing if x["id"] == item_a["id"])
    b = next(x for x in listing if x["id"] == item_b["id"])
    assert a["latest_debate"] is not None
    assert a["latest_debate"]["trigger"] == "automatic"
    assert b["latest_debate"] is None


def test_debate_for_nonexistent_work_item_returns_404(client):
    response = client.get("/api/work-items/999999/debates")
    assert response.status_code == 404
    response = client.post(
        "/api/work-items/999999/debates",
        json={"rounds": 2, "trigger": "manual_rerun"},
    )
    assert response.status_code == 404
    response = client.post(
        "/api/work-items/999999/debate-inputs",
        json={"content": "x", "stance_requested": "neutral"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Debate reset / archive tests
# ---------------------------------------------------------------------------


def test_reset_debate_runs_archives_active_runs(client):
    """POST /debates/reset with mode=archive should soft-hide all active runs."""
    item = _create(client, title="Reset-test")
    # Create a couple of runs
    client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 2, "trigger": "manual_rerun"},
    )
    runs = _debate_list(client, item["id"])
    assert len(runs) == 2

    # Reset
    response = client.post(
        f"/api/work-items/{item['id']}/debates/reset",
        json={"mode": "archive", "reason": "Testing reset"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["archived_count"] == 2
    assert body["hard_deleted"] is False
    assert len(body["archived_run_ids"]) == 2
    assert body["work_item_id"] == item["id"]

    # Active list should now be empty
    runs_after = _debate_list(client, item["id"])
    assert len(runs_after) == 0

    # Hidden list should show archived runs
    hidden = client.get(
        f"/api/work-items/{item['id']}/debates?view=hidden"
    ).json()
    assert len(hidden) == 2

    # All view should show all
    all_runs = client.get(
        f"/api/work-items/{item['id']}/debates?view=all"
    ).json()
    assert len(all_runs) == 2


def test_reset_debate_runs_with_no_runs(client):
    """Reset on an item with no visible (non-hidden) runs should archive 0."""
    item = _create(client, title="No-runs", status="active")  # not debate-eligible, no auto-run
    response = client.post(
        f"/api/work-items/{item['id']}/debates/reset",
        json={"mode": "archive"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["archived_count"] == 0
    assert body["hard_deleted"] is False
    assert body["archived_run_ids"] == []


def test_reset_debate_invalid_mode_rejected(client):
    """Reset with an invalid mode should return 422."""
    item = _create(client, title="Bad-mode")
    response = client.post(
        f"/api/work-items/{item['id']}/debates/reset",
        json={"mode": "wibble"},
    )
    assert response.status_code == 422


def test_reset_debate_hard_delete_permanently_removes_runs(client):
    """POST /debates/reset with mode=hard_delete should permanently delete runs."""
    item = _create(client, title="Hard-delete")
    runs = _debate_list(client, item["id"])
    assert len(runs) == 1

    response = client.post(
        f"/api/work-items/{item['id']}/debates/reset",
        json={"mode": "hard_delete"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["hard_deleted"] is True
    assert body["archived_count"] == 1

    # All views should be empty — hard-deleted runs are gone
    all_runs = client.get(
        f"/api/work-items/{item['id']}/debates?view=all"
    ).json()
    assert len(all_runs) == 0


def test_dashboard_shows_no_stale_failed_after_reset(client):
    """After reset, the work item's latest_debate should be None (no stale FAILED)."""
    item = _create(client, title="Stale-check")
    # Runs exist
    detail = client.get(f"/api/work-items/{item['id']}").json()
    assert detail["latest_debate"] is not None

    # Reset
    client.post(
        f"/api/work-items/{item['id']}/debates/reset",
        json={"mode": "archive"},
    )

    # After reset, latest_debate should be None
    detail_after = client.get(f"/api/work-items/{item['id']}").json()
    assert detail_after["latest_debate"] is None
    assert detail_after["debate_reset_at"] is not None


def test_new_debate_after_reset_works(client):
    """After reset, a new debate run should work normally."""
    item = _create(client, title="After-reset")
    client.post(
        f"/api/work-items/{item['id']}/debates/reset",
        json={"mode": "archive"},
    )

    # Create a new run
    response = client.post(
        f"/api/work-items/{item['id']}/debates",
        json={"rounds": 2, "trigger": "manual_rerun"},
    )
    assert response.status_code == 201
    runs = _debate_list(client, item["id"])
    assert len(runs) == 1


def test_reset_debate_for_nonexistent_work_item_returns_404(client):
    response = client.post(
        "/api/work-items/999999/debates/reset",
        json={"mode": "archive"},
    )
    assert response.status_code == 404
