def _create(client, **overrides):
    payload = {"type": "task", "title": "Sample", "body": "Body"}
    payload.update(overrides)
    response = client.post("/api/work-items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_stats_empty(client):
    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["awaiting_approval"] == 0
    assert data["recent"] == []
    assert data["by_status"]["draft"] == 0
    assert data["by_status"]["completed"] == 0
    assert data["by_type"]["task"] == 0


def test_stats_counts(client):
    _create(client, type="bug", title="B1")
    _create(client, type="bug", title="B2")
    item = _create(client, type="idea", title="I1")
    client.put(f"/api/work-items/{item['id']}", json={"status": "active"})

    response = client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    assert data["awaiting_approval"] == 3
    assert data["by_status"]["draft"] == 2
    assert data["by_status"]["active"] == 1
    assert data["by_type"]["bug"] == 2
    assert data["by_type"]["idea"] == 1


def test_stats_awaiting_approval_decrements(client):
    item = _create(client, title="To approve")
    client.post(f"/api/work-items/{item['id']}/approve")
    _create(client, title="Still pending")

    response = client.get("/api/stats")
    data = response.json()
    assert data["total"] == 2
    assert data["awaiting_approval"] == 1


def test_stats_recent_includes_titles(client):
    _create(client, title="Recent one")
    _create(client, title="Recent two")
    response = client.get("/api/stats")
    titles = [r["title"] for r in response.json()["recent"]]
    assert "Recent one" in titles
    assert "Recent two" in titles


def test_stats_recent_limited(client):
    for i in range(12):
        _create(client, title=f"Item {i}")
    response = client.get("/api/stats")
    assert len(response.json()["recent"]) <= 8
