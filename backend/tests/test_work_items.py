def _create(client, **overrides):
    payload = {"type": "task", "title": "Sample task", "body": "Initial body"}
    payload.update(overrides)
    response = client.post("/api/work-items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_work_item(client):
    data = _create(client, title="Created title")
    assert data["id"] > 0
    assert data["title"] == "Created title"
    assert data["status"] == "draft"
    assert data["approved_by_operator"] is False
    assert data["same_model_blocked"] is False


def test_list_work_items_empty(client):
    response = client.get("/api/work-items")
    assert response.status_code == 200
    assert response.json() == []


def test_list_work_items_returns_created(client):
    _create(client, title="First")
    _create(client, title="Second")
    response = client.get("/api/work-items")
    assert response.status_code == 200
    titles = [item["title"] for item in response.json()]
    assert "First" in titles
    assert "Second" in titles


def test_list_work_items_filter_by_type(client):
    _create(client, type="bug", title="Bug one")
    _create(client, type="idea", title="Idea one")
    response = client.get("/api/work-items", params={"type": "bug"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["type"] == "bug"


def test_list_work_items_filter_by_status(client):
    item = _create(client, title="To approve")
    client.put(f"/api/work-items/{item['id']}", json={"status": "active"})
    _create(client, title="Still draft")
    response = client.get("/api/work-items", params={"status": "active"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["status"] == "active"


def test_get_work_item(client):
    created = _create(client, title="Fetch me")
    response = client.get(f"/api/work-items/{created['id']}")
    assert response.status_code == 200
    assert response.json()["title"] == "Fetch me"


def test_get_work_item_not_found(client):
    response = client.get("/api/work-items/999999")
    assert response.status_code == 404


def test_update_work_item(client):
    created = _create(client, title="Old")
    response = client.put(
        f"/api/work-items/{created['id']}",
        json={"title": "New", "status": "review_needed"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "New"
    assert body["status"] == "review_needed"


def test_update_work_item_not_found(client):
    response = client.put("/api/work-items/424242", json={"title": "x"})
    assert response.status_code == 404


def test_approve_work_item(client):
    created = _create(client, title="Approve me")
    response = client.post(f"/api/work-items/{created['id']}/approve")
    assert response.status_code == 200
    body = response.json()
    assert body["approved_by_operator"] is True
    assert body["approval_timestamp"] is not None


def test_block_work_item(client):
    created = _create(client, title="Block me")
    response = client.post(
        f"/api/work-items/{created['id']}/block",
        json={"override_reason": "Conflicts with main goal"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "blocked"
    assert body["override_reason"] == "Conflicts with main goal"
    assert body["override_timestamp"] is not None


def test_follow_ups_lifecycle(client):
    created = _create(client, title="With follow-ups")
    empty = client.get(f"/api/work-items/{created['id']}/follow-ups")
    assert empty.status_code == 200
    assert empty.json() == []

    response = client.post(
        f"/api/work-items/{created['id']}/follow-ups",
        json={"severity": "blocking", "title": "Missing test", "body": "Add test"},
    )
    assert response.status_code == 201
    follow_up = response.json()
    assert follow_up["severity"] == "blocking"
    assert follow_up["work_item_id"] == created["id"]

    listed = client.get(f"/api/work-items/{created['id']}/follow-ups")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_list_work_items_default_origin_all(client):
    """Default generated=all returns human, system, and test items."""
    _create(client, title="Human item")
    _create(client, title="System item", is_system_generated=True)
    _create(client, title="Test item", is_test_item=True)
    # Default (no generated param) should return all
    response = client.get("/api/work-items")
    assert response.status_code == 200
    titles = [item["title"] for item in response.json()]
    assert "Human item" in titles
    assert "System item" in titles
    assert "Test item" in titles


def test_list_work_items_filter_by_app(client):
    _create(client, title="Dashboard item", target_app="legion-dashboard")
    _create(client, title="Hub item", target_app="lgn-hub")
    _create(client, title="No app item")

    # Filter by specific app
    response = client.get("/api/work-items", params={"app": "legion-dashboard"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "Dashboard item"

    # Filter by another app
    response = client.get("/api/work-items", params={"app": "lgn-hub"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "Hub item"


def test_list_work_items_filter_by_unassigned_app(client):
    _create(client, title="Has app", target_app="legion-dashboard")
    _create(client, title="No app")

    response = client.get("/api/work-items", params={"app": "__unassigned__"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "No app"


def test_list_work_items_app_filter_combined_with_other_filters(client):
    """App filter works together with view, status, type, and generated filters."""
    _create(client, title="Active dashboard bug", type="bug", status="active", target_app="legion-dashboard")
    _create(client, title="Active hub task", type="task", status="active", target_app="lgn-hub")
    _create(client, title="Draft dashboard task", type="task", status="draft", target_app="legion-dashboard")

    # App + type
    response = client.get("/api/work-items", params={"app": "legion-dashboard", "type": "bug"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "Active dashboard bug"

    # App + status
    response = client.get("/api/work-items", params={"app": "legion-dashboard", "status": "draft"})
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["title"] == "Draft dashboard task"


def test_list_work_item_apps(client):
    """Returns distinct non-null target_app values."""
    _create(client, title="A1", target_app="legion-dashboard")
    _create(client, title="A2", target_app="legion-dashboard")
    _create(client, title="B1", target_app="lgn-hub")
    _create(client, title="No app")

    response = client.get("/api/work-items/apps")
    assert response.status_code == 200
    apps = response.json()
    assert "legion-dashboard" in apps
    assert "lgn-hub" in apps
    # No duplicates
    assert len(apps) == len(set(apps))


def test_list_work_item_apps_empty(client):
    """Returns empty list when no work items have target_app."""
    _create(client, title="No app")
    response = client.get("/api/work-items/apps")
    assert response.status_code == 200
    assert response.json() == []
