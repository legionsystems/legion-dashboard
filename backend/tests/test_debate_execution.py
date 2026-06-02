"""Tests for debate execution bridge."""
import pytest
from fastapi.testclient import TestClient


def _create_work_item(client, item_type="idea"):
    """Helper to create a work item."""
    payload = {
        "type": item_type,
        "title": f"Test {item_type.upper()} for debate execution",
        "body": "This is a test work item for debate execution.",
        "status": "draft",
        "priority": "medium",
    }
    response = client.post("/api/work-items", json=payload)
    assert response.status_code == 201
    return response.json()


def _create_debate_run(client, work_item_id, rounds=1):
    """Helper to create a debate run."""
    payload = {"rounds": rounds, "trigger": "manual_rerun"}
    response = client.post(f"/api/work-items/{work_item_id}/debates", json=payload)
    assert response.status_code == 201
    return response.json()


class TestExecutionDisabled:
    """Tests when execution bridge is NOT enabled."""

    def test_execute_endpoint_returns_clear_message(self, client):
        """When execution is disabled, execute returns queued run with clear message."""
        # Create work item
        item = _create_work_item(client)
        work_item_id = item["id"]

        # Create debate run
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Try to execute
        response = client.post(f"/api/work-items/{work_item_id}/debates/{run_id}/execute")
        assert response.status_code == 200
        data = response.json()

        # Should still be queued
        assert data["status"] == "queued"
        # Should have clear error message
        assert "not enabled" in data["error_message"].lower() or "disabled" in data["error_message"].lower()
        assert "Settings" in data["error_message"] or "DEBATE_EXECUTION_ENABLED" in data["error_message"]

    def test_execute_next_returns_clear_message(self, client):
        """Execute next also returns clear message when disabled."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        # Create debate run
        _create_debate_run(client, work_item_id, rounds=1)

        # Execute next
        response = client.get(f"/api/work-items/{work_item_id}/debates/next")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "queued"
        assert "not enabled" in data["error_message"].lower() or "disabled" in data["error_message"].lower()


class TestExecutionEndpointsExist:
    """Tests that execution endpoints exist and accept valid requests."""

    def test_execute_endpoint_exists(self, client):
        """Execute endpoint exists and accepts POST."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Should not 404
        response = client.post(f"/api/work-items/{work_item_id}/debates/{run_id}/execute")
        assert response.status_code == 200

    def test_execute_next_endpoint_exists(self, client):
        """Execute next endpoint exists and accepts GET."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        # Should not 404
        response = client.get(f"/api/work-items/{work_item_id}/debates/next")
        assert response.status_code == 200

    def test_execute_nonexistent_run_returns_404(self, client):
        """Execute on nonexistent run returns 404."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        response = client.post(f"/api/work-items/{work_item_id}/debates/999999/execute")
        assert response.status_code == 404

    def test_execute_on_nonexistent_item_returns_404(self, client):
        """Execute on nonexistent work item returns 404."""
        response = client.post("/api/work-items/999999/debates/1/execute")
        assert response.status_code == 404


class TestDebateWorksForAllTypes:
    """Test that debate can be queued for all work item types."""

    @pytest.mark.parametrize("item_type", ["idea", "bug", "change", "task", "slice"])
    def test_debate_queues_for_type(self, client, item_type):
        """Debate auto-queues for all work item types."""
        item = _create_work_item(client, item_type)

        # Should have latest_debate populated
        assert item["latest_debate"] is not None
        assert item["latest_debate"]["status"] == "queued"
        assert item["latest_debate"]["trigger"] == "automatic"


class TestRoundsValidation:
    """Test rounds validation in execution context."""

    def test_rounds_outside_range_rejected(self, client):
        """Rounds outside 1-5 rejected."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        for bad_rounds in [0, -1, 6, 99]:
            payload = {"rounds": bad_rounds, "trigger": "manual_rerun"}
            response = client.post(f"/api/work-items/{work_item_id}/debates", json=payload)
            assert response.status_code == 422, f"Expected 422 for rounds={bad_rounds}"

    def test_rounds_inside_range_accepted(self, client):
        """Rounds 1-5 accepted."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        for good_rounds in [1, 2, 3, 4, 5]:
            payload = {"rounds": good_rounds, "trigger": "manual_rerun"}
            response = client.post(f"/api/work-items/{work_item_id}/debates", json=payload)
            assert response.status_code == 201, f"Expected 201 for rounds={good_rounds}"
            data = response.json()
            assert data["rounds_requested"] == good_rounds


class TestDebateDoesNotMutateWorkItem:
    """Safety tests: debate does not approve or implement work."""

    def test_debate_does_not_approve_work_item(self, client):
        """Debate execution does not set approved_by_operator."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        # Create and "execute" debate
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]
        client.post(f"/api/work-items/{work_item_id}/debates/{run_id}/execute")

        # Check work item is still not approved
        response = client.get(f"/api/work-items/{work_item_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["approved_by_operator"] is False
        assert data["pr_url"] is None
        assert data["merge_commit_sha"] is None

    def test_debate_does_not_set_delivery_fields(self, client):
        """Debate does not set PR URL or merge commit."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]
        client.post(f"/api/work-items/{work_item_id}/debates/{run_id}/execute")

        response = client.get(f"/api/work-items/{work_item_id}")
        data = response.json()
        assert data["pr_url"] is None
        assert data["merge_commit_sha"] is None


class TestOperatorArguments:
    """Test operator argument handling."""

    def test_operator_argument_persists(self, client):
        """Operator argument persists and is visible."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        # Add operator argument
        payload = {
            "content": "This is a test operator argument.",
            "stance_requested": "pro",
        }
        response = client.post(f"/api/work-items/{work_item_id}/debate-inputs", json=payload)
        assert response.status_code == 201

        # List inputs
        response = client.get(f"/api/work-items/{work_item_id}/debate-inputs")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["content"] == "This is a test operator argument."
        assert data[0]["stance_requested"] == "pro"

    def test_operator_argument_rejects_empty_content(self, client):
        """Empty operator argument rejected."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        payload = {"content": "   ", "stance_requested": "pro"}
        response = client.post(f"/api/work-items/{work_item_id}/debate-inputs", json=payload)
        assert response.status_code == 422

    def test_operator_argument_rejects_invalid_stance(self, client):
        """Invalid stance rejected."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        payload = {"content": "Test", "stance_requested": "invalid"}
        response = client.post(f"/api/work-items/{work_item_id}/debate-inputs", json=payload)
        assert response.status_code == 422


class TestDebateHistoryPreserved:
    """Test that old debate runs are preserved on rerun."""

    def test_rerun_creates_new_run_preserves_old(self, client):
        """Manual rerun creates new run, old run unchanged."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        # Create first run (manual)
        run1 = _create_debate_run(client, work_item_id, rounds=1)
        run1_id = run1["id"]

        # Create second run (rerun)
        run2 = _create_debate_run(client, work_item_id, rounds=2)
        run2_id = run2["id"]

        # Both should exist
        assert run1_id != run2_id
        assert run2["rounds_requested"] == 2

        # List runs - note: work item creation also auto-queues a run
        # so we expect 3 total (auto + 2 manual)
        response = client.get(f"/api/work-items/{work_item_id}/debates")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2
        # Most recent first
        assert data[0]["id"] == run2_id
        assert data[1]["id"] == run1_id


class TestModelProvenanceStored:
    """Test that model provenance is stored without secrets."""

    def test_provenance_stored_without_secrets(self, client):
        """Provenance field stores model info, not secrets."""
        item = _create_work_item(client)
        work_item_id = item["id"]

        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Execute (will fail due to disabled bridge)
        response = client.post(f"/api/work-items/{work_item_id}/debates/{run_id}/execute")
        data = response.json()

        # Provenance should not contain API key patterns
        provenance = data.get("provenance") or ""
        assert "sk-" not in provenance.lower()
        assert "key=" not in provenance.lower()
        assert "secret" not in provenance.lower()
