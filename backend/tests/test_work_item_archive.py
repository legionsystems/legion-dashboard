"""Tests for work item archive system and classification filters."""
import pytest
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from app.models import WorkItem


@pytest.fixture
def clean_items(db_session):
    """Clean work items before test."""
    db_session.query(WorkItem).delete()
    db_session.commit()
    yield


class TestArchiveLifecycle:
    """Test archive and restore functionality."""

    def test_archive_work_item(self, client, clean_items):
        """Archive a work item."""
        # Create work item
        response = client.post(
            "/api/work-items",
            json={"title": "Test Item", "type": "task"},
        )
        assert response.status_code == 201
        item_id = response.json()["id"]

        # Archive it
        response = client.post(
            f"/api/work-items/{item_id}/archive",
            json={"reason": "Testing archive"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["archived"] is True
        assert data["archive_reason"] == "Testing archive"
        assert data["archived_at"] is not None

    def test_archive_already_archived_fails(self, client, clean_items):
        """Archiving an already archived item fails."""
        # Create and archive
        response = client.post(
            "/api/work-items",
            json={"title": "Test Item", "type": "task"},
        )
        item_id = response.json()["id"]
        client.post(f"/api/work-items/{item_id}/archive", json={})

        # Try to archive again
        response = client.post(f"/api/work-items/{item_id}/archive", json={})
        assert response.status_code == 400
        assert "already archived" in response.json()["detail"]

    def test_restore_work_item(self, client, clean_items):
        """Restore an archived work item."""
        # Create and archive
        response = client.post(
            "/api/work-items",
            json={"title": "Test Item", "type": "task"},
        )
        item_id = response.json()["id"]
        client.post(f"/api/work-items/{item_id}/archive", json={})

        # Restore it
        response = client.post(f"/api/work-items/{item_id}/restore")
        assert response.status_code == 200
        data = response.json()
        assert data["archived"] is False
        assert data["archive_reason"] is None

    def test_restore_non_archived_fails(self, client, clean_items):
        """Restoring a non-archived item fails."""
        response = client.post(
            "/api/work-items",
            json={"title": "Test Item", "type": "task"},
        )
        item_id = response.json()["id"]

        response = client.post(f"/api/work-items/{item_id}/restore")
        assert response.status_code == 400
        assert "not archived" in response.json()["detail"]

    def test_archive_preserves_debates(self, client, clean_items):
        """Archiving preserves debate history."""
        # Create work item
        response = client.post(
            "/api/work-items",
            json={"title": "Test Item", "type": "task"},
        )
        item_id = response.json()["id"]

        # Create a debate run
        response = client.post(
            f"/api/work-items/{item_id}/debates",
            json={"rounds": 1, "trigger": "manual_rerun"},
        )
        assert response.status_code == 201

        # Archive the work item
        response = client.post(f"/api/work-items/{item_id}/archive", json={})
        assert response.status_code == 200

        # Verify debate still accessible
        response = client.get(f"/api/work-items/{item_id}/debates")
        assert response.status_code == 200
        assert len(response.json()) > 0


class TestArchiveViewFilter:
    """Test view=active|archived|all filters."""

    def test_view_active_excludes_archived(self, client, clean_items):
        """Active view excludes archived items."""
        # Create active item
        client.post("/api/work-items", json={"title": "Active Item", "type": "task"})
        
        # Create and archive another
        response = client.post("/api/work-items", json={"title": "Archived Item", "type": "task"})
        client.post(f"/api/work-items/{response.json()['id']}/archive", json={})

        # Get active view
        response = client.get("/api/work-items?view=active")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["title"] == "Active Item"

    def test_view_archived_only(self, client, clean_items):
        """Archived view shows only archived items."""
        # Create active item
        client.post("/api/work-items", json={"title": "Active Item", "type": "task"})
        
        # Create and archive another
        response = client.post("/api/work-items", json={"title": "Archived Item", "type": "task"})
        archived_id = response.json()["id"]
        client.post(f"/api/work-items/{archived_id}/archive", json={})

        # Get archived view
        response = client.get("/api/work-items?view=archived")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["title"] == "Archived Item"

    def test_view_all_includes_both(self, client, clean_items):
        """All view includes both active and archived."""
        # Create active item
        client.post("/api/work-items", json={"title": "Active Item", "type": "task"})
        
        # Create and archive another
        response = client.post("/api/work-items", json={"title": "Archived Item", "type": "task"})
        client.post(f"/api/work-items/{response.json()['id']}/archive", json={})

        # Get all view
        response = client.get("/api/work-items?view=all")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 2


class TestGeneratedFilter:
    """Test generated=human|system|test|all filters."""

    def test_generated_human_excludes_system_test(self, client, clean_items):
        """Human filter excludes system-generated and test items."""
        # Create human item
        client.post("/api/work-items", json={"title": "Human Item", "type": "task"})
        
        # Create system-generated item
        client.post("/api/work-items", json={
            "title": "System Item",
            "type": "task",
            "is_system_generated": True,
        })
        
        # Create test item
        client.post("/api/work-items", json={
            "title": "Test Item",
            "type": "task",
            "is_test_item": True,
        })

        # Get human view
        response = client.get("/api/work-items?generated=human")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["title"] == "Human Item"

    def test_generated_system_only(self, client, clean_items):
        """System filter shows only system-generated items."""
        # Create human item
        client.post("/api/work-items", json={"title": "Human Item", "type": "task"})
        
        # Create system-generated item
        response = client.post("/api/work-items", json={
            "title": "System Item",
            "type": "task",
            "is_system_generated": True,
            "generated_by": "hermes",
        })

        # Get system view
        response = client.get("/api/work-items?generated=system")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["title"] == "System Item"
        assert items[0]["is_system_generated"] is True
        assert items[0]["generated_by"] == "hermes"

    def test_generated_test_only(self, client, clean_items):
        """Test filter shows only test items."""
        # Create human item
        client.post("/api/work-items", json={"title": "Human Item", "type": "task"})
        
        # Create test item
        response = client.post("/api/work-items", json={
            "title": "Test Item",
            "type": "task",
            "is_test_item": True,
            "generated_by": "debate-smoke-test",
        })

        # Get test view
        response = client.get("/api/work-items?generated=test")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["title"] == "Test Item"
        assert items[0]["is_test_item"] is True

    def test_combined_filters(self, client, clean_items):
        """Combined view and generated filters work together."""
        # Create active human item
        client.post("/api/work-items", json={"title": "Active Human", "type": "task"})
        
        # Create archived human item
        response = client.post("/api/work-items", json={"title": "Archived Human", "type": "task"})
        client.post(f"/api/work-items/{response.json()['id']}/archive", json={})
        
        # Create active test item
        client.post("/api/work-items", json={
            "title": "Active Test",
            "type": "task",
            "is_test_item": True,
        })

        # Get active + human
        response = client.get("/api/work-items?view=active&generated=human")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 1
        assert items[0]["title"] == "Active Human"


class TestClassificationUpdate:
    """Test classification update endpoint."""

    def test_update_classification(self, client, clean_items):
        """Update work item classification."""
        response = client.post(
            "/api/work-items",
            json={"title": "Test Item", "type": "task"},
        )
        item_id = response.json()["id"]

        # Update classification
        response = client.patch(
            f"/api/work-items/{item_id}/classification",
            json={"is_system_generated": True, "is_test_item": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["is_system_generated"] is True
        assert data["is_test_item"] is True


class TestBulkArchive:
    """Test bulk archive endpoints."""

    def test_bulk_archive(self, client, clean_items):
        """Bulk archive multiple items."""
        # Create items
        ids = []
        for i in range(3):
            response = client.post(
                "/api/work-items",
                json={"title": f"Item {i}", "type": "task"},
            )
            ids.append(response.json()["id"])

        # Bulk archive
        response = client.post(
            "/api/work-items/bulk/archive",
            json={"ids": ids, "reason": "Bulk test"},
        )
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 3
        assert all(item["archived"] is True for item in items)

    def test_bulk_archive_test_items_dry_run(self, client, clean_items):
        """Bulk archive test items dry run."""
        # Create test items
        for i in range(3):
            client.post("/api/work-items", json={
                "title": f"Test {i}",
                "type": "task",
                "is_test_item": True,
            })

        # Dry run
        response = client.post(
            "/api/work-items/bulk/archive-test-items",
            json={"dry_run": True},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is True
        assert data["would_archive_count"] == 3
        assert len(data["would_archive_ids"]) == 3

    def test_bulk_archive_test_items_actual(self, client, clean_items):
        """Bulk archive test items actual run."""
        # Create test items
        for i in range(3):
            client.post("/api/work-items", json={
                "title": f"Test {i}",
                "type": "task",
                "is_test_item": True,
            })

        # Actual run
        response = client.post(
            "/api/work-items/bulk/archive-test-items",
            json={"dry_run": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["dry_run"] is False
        assert data["archived_count"] == 3


class TestExistingItemsDefaultActive:
    """Test that existing items default to active."""

    def test_new_item_is_active(self, client, clean_items):
        """New work items are active by default."""
        response = client.post(
            "/api/work-items",
            json={"title": "New Item", "type": "task"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["archived"] is False
        assert data["is_system_generated"] is False
        assert data["is_test_item"] is False
