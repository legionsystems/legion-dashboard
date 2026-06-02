"""Tests for slice 2 — operator certification, rejection, and change-request
endpoints on work items.

Endpoints under test:
- POST /api/work-items/{id}/certify
- POST /api/work-items/{id}/reject
- POST /api/work-items/{id}/reject-with-changes

These actions are operator decisions — they do not merge, close, or open PRs.
They only mutate Work Item metadata (certification fields, rejection fields,
change-request fields) and surface via ``effective_state``.

Valid source effective states for all three actions:
    in_review, preview_ready, code_reviewed.

Invalid transitions return 409.
"""
from app.models import WorkItem


def _create(client, **overrides):
    payload = {"type": "task", "title": "Slice 2 candidate"}
    payload.update(overrides)
    response = client.post("/api/work-items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _set_in_review(db_session, work_item_id, **extra):
    """Promote a freshly-created work item to ``in_review`` by setting a
    PR number. Optional extra fields can layer on preview/code-review state.
    """
    item = (
        db_session.query(WorkItem)
        .filter(WorkItem.id == work_item_id)
        .one()
    )
    item.pr_number = 101
    for k, v in extra.items():
        setattr(item, k, v)
    db_session.commit()


# ---------------------------------------------------------------------------
# Certify
# ---------------------------------------------------------------------------


def test_certify_success_from_in_review(client, db_session):
    created = _create(client, title="Certify me")
    _set_in_review(db_session, created["id"])

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={"certification_note": "LGTM, ship it."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operator_certified"] is True
    assert body["certified_at"] is not None
    assert body["certification_note"] == "LGTM, ship it."
    assert body["effective_state"] == "certified"


def test_certify_success_without_note(client, db_session):
    created = _create(client, title="Certify silent")
    _set_in_review(db_session, created["id"])

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operator_certified"] is True
    assert body["certification_note"] is None


def test_certify_success_from_preview_ready(client, db_session):
    created = _create(client, title="Preview ready")
    _set_in_review(db_session, created["id"], preview_deployed=True)

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={},
    )

    assert response.status_code == 200, response.text
    assert response.json()["effective_state"] == "certified"


def test_certify_success_from_code_reviewed(client, db_session):
    created = _create(client, title="Code reviewed")
    _set_in_review(db_session, created["id"], code_review_status="approved")

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={},
    )

    assert response.status_code == 200, response.text
    assert response.json()["effective_state"] == "certified"


def test_certify_invalid_transition_from_draft_returns_409(client):
    created = _create(client, title="Still drafting")

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={},
    )

    assert response.status_code == 409


def test_certify_already_certified_returns_409(client, db_session):
    created = _create(client, title="Already certified")
    _set_in_review(db_session, created["id"])
    # First certify succeeds.
    first = client.post(f"/api/work-items/{created['id']}/certify", json={})
    assert first.status_code == 200

    # Second certify must be rejected.
    second = client.post(f"/api/work-items/{created['id']}/certify", json={})
    assert second.status_code == 409


def test_certify_already_merged_returns_409(client, db_session):
    created = _create(client, title="Already merged")
    item = db_session.query(WorkItem).filter(WorkItem.id == created["id"]).one()
    item.merge_commit_sha = "abc123"
    db_session.commit()

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={},
    )

    assert response.status_code == 409


def test_certify_archived_returns_409(client, db_session):
    created = _create(client, title="Already archived")
    item = db_session.query(WorkItem).filter(WorkItem.id == created["id"]).one()
    item.archived = True
    db_session.commit()

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={},
    )

    assert response.status_code == 409


def test_certify_already_rejected_returns_409(client, db_session):
    created = _create(client, title="Already rejected")
    _set_in_review(db_session, created["id"])
    reject = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "Not viable."},
    )
    assert reject.status_code == 200

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={},
    )

    assert response.status_code == 409


def test_certify_completed_kanban_status_returns_409(client, db_session):
    """A work item whose Kanban status is ``completed`` (effective_state =
    ``implemented``) must not be certifiable — certification is gated on
    in_review/preview_ready/code_reviewed.
    """
    created = _create(client, title="Implementation done")
    item = db_session.query(WorkItem).filter(WorkItem.id == created["id"]).one()
    item.status = "completed"
    db_session.commit()

    response = client.post(
        f"/api/work-items/{created['id']}/certify",
        json={},
    )

    assert response.status_code == 409


def test_certify_not_found(client):
    response = client.post("/api/work-items/99999/certify", json={})
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Reject
# ---------------------------------------------------------------------------


def test_reject_requires_reason(client, db_session):
    created = _create(client, title="Needs reason")
    _set_in_review(db_session, created["id"])

    # Missing key — pydantic rejects with 422.
    missing = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={},
    )
    assert missing.status_code == 422

    # Empty string — validator rejects with 422.
    blank = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "   "},
    )
    assert blank.status_code == 422


def test_reject_success_from_in_review(client, db_session):
    created = _create(client, title="Reject me")
    _set_in_review(db_session, created["id"])

    response = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "Out of scope for this sprint."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rejected_at"] is not None
    assert body["rejection_reason"] == "Out of scope for this sprint."
    assert body["effective_state"] == "rejected"


def test_reject_invalid_transition_from_draft_returns_409(client):
    created = _create(client, title="Still drafting")

    response = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "nope"},
    )

    assert response.status_code == 409


def test_reject_already_certified_returns_409(client, db_session):
    created = _create(client, title="Certified then rejected")
    _set_in_review(db_session, created["id"])
    cert = client.post(f"/api/work-items/{created['id']}/certify", json={})
    assert cert.status_code == 200

    response = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "Changed my mind."},
    )

    assert response.status_code == 409


def test_reject_already_merged_returns_409(client, db_session):
    created = _create(client, title="Already merged")
    item = db_session.query(WorkItem).filter(WorkItem.id == created["id"]).one()
    item.merge_commit_sha = "abc123"
    db_session.commit()

    response = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "Too late."},
    )

    assert response.status_code == 409


def test_reject_archived_returns_409(client, db_session):
    created = _create(client, title="Already archived")
    item = db_session.query(WorkItem).filter(WorkItem.id == created["id"]).one()
    item.archived = True
    db_session.commit()

    response = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "Already gone."},
    )

    assert response.status_code == 409


def test_reject_already_rejected_returns_409(client, db_session):
    created = _create(client, title="Reject twice")
    _set_in_review(db_session, created["id"])
    first = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "first"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/work-items/{created['id']}/reject",
        json={"rejection_reason": "second"},
    )
    assert second.status_code == 409


def test_reject_not_found(client):
    response = client.post(
        "/api/work-items/99999/reject",
        json={"rejection_reason": "x"},
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Reject with changes (request rework)
# ---------------------------------------------------------------------------


def test_reject_with_changes_requires_change_request(client, db_session):
    created = _create(client, title="Needs change request")
    _set_in_review(db_session, created["id"])

    missing = client.post(
        f"/api/work-items/{created['id']}/reject-with-changes",
        json={},
    )
    assert missing.status_code == 422

    blank = client.post(
        f"/api/work-items/{created['id']}/reject-with-changes",
        json={"change_request": "  "},
    )
    assert blank.status_code == 422


def test_reject_with_changes_success_from_in_review(client, db_session):
    created = _create(client, title="Needs rework")
    _set_in_review(db_session, created["id"])

    response = client.post(
        f"/api/work-items/{created['id']}/reject-with-changes",
        json={"change_request": "Please add input validation and re-submit."},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changes_requested_at"] is not None
    assert (
        body["change_request"]
        == "Please add input validation and re-submit."
    )
    assert body["effective_state"] == "needs_rework"


def test_reject_with_changes_invalid_transition_from_draft_returns_409(client):
    created = _create(client, title="Still drafting")

    response = client.post(
        f"/api/work-items/{created['id']}/reject-with-changes",
        json={"change_request": "fix it"},
    )

    assert response.status_code == 409


def test_reject_with_changes_already_certified_returns_409(client, db_session):
    created = _create(client, title="Certified then rework")
    _set_in_review(db_session, created["id"])
    cert = client.post(f"/api/work-items/{created['id']}/certify", json={})
    assert cert.status_code == 200

    response = client.post(
        f"/api/work-items/{created['id']}/reject-with-changes",
        json={"change_request": "redo it"},
    )

    assert response.status_code == 409


def test_reject_with_changes_already_merged_returns_409(client, db_session):
    created = _create(client, title="Already merged")
    item = db_session.query(WorkItem).filter(WorkItem.id == created["id"]).one()
    item.merge_commit_sha = "abc123"
    db_session.commit()

    response = client.post(
        f"/api/work-items/{created['id']}/reject-with-changes",
        json={"change_request": "too late"},
    )

    assert response.status_code == 409


def test_reject_with_changes_not_found(client):
    response = client.post(
        "/api/work-items/99999/reject-with-changes",
        json={"change_request": "x"},
    )
    assert response.status_code == 404
