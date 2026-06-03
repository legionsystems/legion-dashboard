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


class TestRerunArbiter:
    """Tests for POST /api/work-items/{id}/debates/{run_id}/rerun-arbiter."""

    def test_rerun_arbiter_nonexistent_run_returns_404(self, client):
        """Rerun arbiter on nonexistent run returns 404."""
        item = _create_work_item(client)
        work_item_id = item["id"]
        response = client.post(
            f"/api/work-items/{work_item_id}/debates/999999/rerun-arbiter", json={}
        )
        assert response.status_code == 404

    def test_rerun_arbiter_nonexistent_item_returns_404(self, client):
        """Rerun arbiter on nonexistent work item returns 404."""
        response = client.post(
            "/api/work-items/999999/debates/1/rerun-arbiter", json={}
        )
        assert response.status_code == 404

    def test_rerun_arbiter_completed_run_rejected(self, client):
        """Cannot rerun arbiter on a completed run."""
        from unittest.mock import patch as mock_patch, MagicMock
        from app.debate_executor import ExecutionConfig

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Manually set run to completed with a recommendation
        client.put(f"/api/work-items/{work_item_id}", json={"status": "draft"})
        # Directly update via DB to simulate completed run
        from app.database import SessionLocal
        from app.models import DebateRun
        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "completed"
        db_run.final_recommendation = "APPROVE_AS_IS"
        db_run.execution_stage = "completed"
        db.commit()
        db.close()

        response = client.post(
            f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
        )
        assert response.status_code == 400
        assert "completed" in response.json()["detail"].lower()

    def test_rerun_arbiter_queued_run_rejected(self, client):
        """Cannot rerun arbiter on a queued run (in progress, not failed)."""
        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        response = client.post(
            f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
        )
        assert response.status_code == 409
        assert "in progress" in response.json()["detail"].lower()

    def test_rerun_arbiter_no_arguments_rejected(self, client):
        """Cannot rerun arbiter when run has no PRO/CON arguments."""
        from app.database import SessionLocal
        from app.models import DebateRun

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Set run to failed with arbiter error, but no arguments
        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.error_stage = "arbiter"
        db_run.error_message = "Arbiter failed"
        db_run.execution_stage = "failed"
        db_run.provenance = "test"  # Override default "execution-disabled"
        db.commit()
        db.close()

        response = client.post(
            f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
        )
        assert response.status_code == 400
        assert "PRO/CON arguments" in response.json()["detail"]

    def test_rerun_arbiter_execution_disabled_rejected(self, client):
        """Cannot rerun arbiter when execution was disabled."""
        from app.database import SessionLocal
        from app.models import DebateRun

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Set run to failed with execution-disabled provenance
        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.provenance = "execution-disabled"
        db.commit()
        db.close()

        response = client.post(
            f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
        )
        assert response.status_code == 400
        assert "execution bridge was not configured" in response.json()["detail"].lower()

    def test_rerun_arbiter_increments_count(self, client):
        """Rerun arbiter increments arbiter_rerun_count."""
        from unittest.mock import patch as mock_patch, MagicMock
        from app.debate_executor import ExecutionConfig, _execute_arbiter_turn
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Seed PRO/CON arguments and set run to failed with arbiter error
        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.error_stage = "arbiter"
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument",
        ))
        db.commit()
        initial_count = db_run.arbiter_rerun_count
        db.close()

        mock_arbiter_result = {
            "success": False,
            "data": None,
            "failure_category": "invalid_json",
            "safe_diagnostic": "test mock",
            "was_repaired": False,
        }

        mock_config = ExecutionConfig(
            enabled=True, provider="ollama_native",
            base_url="http://localhost:11434/v1",
            model="test-model", timeout_seconds=30,
        )

        with mock_patch(
            "app.routers.work_items.get_execution_config", return_value=mock_config
        ), mock_patch(
            "app.debate_executor._execute_arbiter_turn",
            return_value=mock_arbiter_result,
        ):
            response = client.post(
                f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
            )

        assert response.status_code == 200
        data = response.json()
        # Count should have incremented from initial (0) to 1
        assert data["arbiter_rerun_count"] == initial_count + 1

    def test_rerun_arbiter_success_clears_stale_errors(self, client):
        """Successful arbiter rerun clears error fields and sets completed."""
        from unittest.mock import patch as mock_patch
        from app.debate_executor import ExecutionConfig
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.error_stage = "arbiter"
        db_run.error_message = "Previous arbiter failure"
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument",
        ))
        db.commit()
        db.close()

        mock_arbiter_result = {
            "success": True,
            "data": {
                "recommendation": "APPROVE_AS_IS",
                "implementation_readiness": "READY_NOW",
                "rationale": "Mock arbiter approved",
            },
            "failure_category": None,
            "safe_diagnostic": None,
            "was_repaired": False,
        }

        mock_config = ExecutionConfig(
            enabled=True, provider="ollama_native",
            base_url="http://localhost:11434/v1",
            model="test-model", timeout_seconds=30,
        )

        with mock_patch(
            "app.routers.work_items.get_execution_config", return_value=mock_config
        ), mock_patch(
            "app.debate_executor._execute_arbiter_turn",
            return_value=mock_arbiter_result,
        ):
            response = client.post(
                f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["final_recommendation"] == "APPROVE_AS_IS"
        assert data["implementation_readiness"] == "READY_NOW"
        assert data["summary"] == "Mock arbiter approved"
        assert data["error_type"] is None
        assert data["error_stage"] is None
        assert data["error_message"] is None

    def test_rerun_arbiter_failed_preserves_turns_and_shows_no_decision(self, client):
        """Failed arbiter rerun preserves existing turns and shows NO_DECISION reason."""
        from unittest.mock import patch as mock_patch
        from app.debate_executor import ExecutionConfig
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument for testing",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument for testing",
        ))
        db.commit()
        db.close()

        mock_arbiter_result = {
            "success": False,
            "data": None,
            "failure_category": "invalid_json",
            "safe_diagnostic": "No valid JSON found in response",
            "was_repaired": False,
        }

        mock_config = ExecutionConfig(
            enabled=True, provider="ollama_native",
            base_url="http://localhost:11434/v1",
            model="test-model", timeout_seconds=30,
        )

        with mock_patch(
            "app.routers.work_items.get_execution_config", return_value=mock_config
        ), mock_patch(
            "app.debate_executor._execute_arbiter_turn",
            return_value=mock_arbiter_result,
        ):
            response = client.post(
                f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error_type"] == "arbiter_invalid_json"
        assert data["error_stage"] == "arbiter"
        assert "could not reach a decision" in data["error_message"].lower()
        # Existing PRO/CON arguments should be preserved (may also have system neutral args)
        args = data.get("arguments", [])
        assert len(args) >= 2
        sides = [a["side"] for a in args]
        assert "pro" in sides
        assert "con" in sides

    def test_rerun_arbiter_increments_count_on_failure(self, client):
        """Failed arbiter rerun also increments count."""
        from unittest.mock import patch as mock_patch
        from app.debate_executor import ExecutionConfig
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument",
        ))
        db.commit()
        db.close()

        mock_arbiter_result = {
            "success": False,
            "data": None,
            "failure_category": "invalid_json",
            "safe_diagnostic": "No JSON found",
            "was_repaired": False,
        }

        mock_config = ExecutionConfig(
            enabled=True, provider="ollama_native",
            base_url="http://localhost:11434/v1",
            model="test-model", timeout_seconds=30,
        )

        with mock_patch(
            "app.routers.work_items.get_execution_config", return_value=mock_config
        ), mock_patch(
            "app.debate_executor._execute_arbiter_turn",
            return_value=mock_arbiter_result,
        ):
            response = client.post(
                f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["arbiter_rerun_count"] == 1

    def test_rerun_arbiter_updates_summary_on_success(self, client):
        """Successful arbiter rerun updates the summary field."""
        from unittest.mock import patch as mock_patch
        from app.debate_executor import ExecutionConfig
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument",
        ))
        db.commit()
        db.close()

        mock_arbiter_result = {
            "success": True,
            "data": {
                "recommendation": "APPROVE_WITH_MANDATORY_EDITS",
                "implementation_readiness": "READY_AFTER_EDITS",
                "rationale": "Approved with mandatory edits needed",
            },
            "failure_category": None,
            "safe_diagnostic": None,
            "was_repaired": False,
        }

        mock_config = ExecutionConfig(
            enabled=True, provider="ollama_native",
            base_url="http://localhost:11434/v1",
            model="test-model", timeout_seconds=30,
        )

        with mock_patch(
            "app.routers.work_items.get_execution_config", return_value=mock_config
        ), mock_patch(
            "app.debate_executor._execute_arbiter_turn",
            return_value=mock_arbiter_result,
        ):
            response = client.post(
                f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["summary"] == "Approved with mandatory edits needed"
        assert data["final_recommendation"] == "APPROVE_WITH_MANDATORY_EDITS"

    def test_rerun_arbiter_success_advances_draft_work_item_to_debated(self, client):
        """Successful arbiter rerun advances a draft Work Item to debated."""
        from unittest.mock import patch as mock_patch
        from app.debate_executor import ExecutionConfig
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument, WorkItem

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.error_stage = "arbiter"
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument",
        ))
        db.commit()

        # Verify work item is still draft before rerun
        db_item = db.query(WorkItem).filter(WorkItem.id == work_item_id).first()
        assert db_item.status == "draft"
        db.close()

        mock_arbiter_result = {
            "success": True,
            "data": {
                "recommendation": "APPROVE_AS_IS",
                "implementation_readiness": "READY_NOW",
                "rationale": "Mock arbiter approved on rerun",
            },
            "failure_category": None,
            "safe_diagnostic": None,
            "was_repaired": False,
        }

        mock_config = ExecutionConfig(
            enabled=True, provider="ollama_native",
            base_url="http://localhost:11434/v1",
            model="test-model", timeout_seconds=30,
        )

        with mock_patch(
            "app.routers.work_items.get_execution_config", return_value=mock_config
        ), mock_patch(
            "app.debate_executor._execute_arbiter_turn",
            return_value=mock_arbiter_result,
        ):
            response = client.post(
                f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"

        # Verify work item was advanced to debated
        db2 = SessionLocal()
        db_item2 = db2.query(WorkItem).filter(WorkItem.id == work_item_id).first()
        assert db_item2.status == "debated"
        db2.close()

    def test_rerun_arbiter_concurrent_request_rejected(self, client):
        """A second arbiter rerun request is rejected while the run is in progress."""
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Set run to 'running' to simulate an in-progress arbiter rerun
        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "running"
        db_run.execution_stage = "running"
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument",
        ))
        db.commit()
        db.close()

        # Second request should be rejected with 409
        response = client.post(
            f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
        )
        assert response.status_code == 409
        assert "in progress" in response.json()["detail"].lower()

    def test_rerun_arbiter_null_error_type_rejected(self, client):
        """Cannot rerun arbiter on a failed run with null error_type."""
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        # Set run to failed with NO error_type (null)
        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = None
        db_run.error_stage = None
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument",
        ))
        db.commit()
        db.close()

        response = client.post(
            f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
        )
        assert response.status_code == 400
        assert "arbiter failure" in response.json()["detail"].lower()

    def test_rerun_arbiter_execution_exception_sets_failed_stage(self, client):
        """When _execute_arbiter_turn raises, execution_stage must be 'failed', not 'running'."""
        from unittest.mock import patch as mock_patch
        from app.debate_executor import ExecutionConfig
        from app.database import SessionLocal
        from app.models import DebateRun, DebateArgument

        item = _create_work_item(client)
        work_item_id = item["id"]
        run = _create_debate_run(client, work_item_id, rounds=1)
        run_id = run["id"]

        db = SessionLocal()
        db_run = db.query(DebateRun).filter(DebateRun.id == run_id).first()
        db_run.status = "failed"
        db_run.error_type = "arbiter_failure"
        db_run.provenance = "test"
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Product Owner",
            side="pro", content="PRO argument",
        ))
        db.add(DebateArgument(
            debate_run_id=run_id, round_number=1, role="Skeptic",
            side="con", content="CON argument",
        ))
        db.commit()
        db.close()

        mock_config = ExecutionConfig(
            enabled=True, provider="ollama_native",
            base_url="http://localhost:11434/v1",
            model="test-model", timeout_seconds=30,
        )

        with mock_patch(
            "app.routers.work_items.get_execution_config", return_value=mock_config
        ), mock_patch(
            "app.debate_executor._execute_arbiter_turn",
            side_effect=Exception("model timeout"),
        ):
            response = client.post(
                f"/api/work-items/{work_item_id}/debates/{run_id}/rerun-arbiter", json={}
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["execution_stage"] == "failed"
        assert data["error_stage"] == "arbiter"
        assert data["error_type"] == "arbiter_failure"
        assert "Arbiter rerun failed" in data["error_message"]
        # Verify progress_message reflects failure, not stale "running" message
        assert "failed" in data.get("progress_message", "").lower()
