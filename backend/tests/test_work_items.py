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
