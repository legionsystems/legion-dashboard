import io
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _create_work_item(client, **overrides):
    payload = {"type": "task", "title": "Sample task", "body": "Initial body"}
    payload.update(overrides)
    response = client.post("/api/work-items", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_attachment_png(client):
    """Upload a PNG image attachment to a work item."""
    item = _create_work_item(client, title="With attachment")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00"
        b"IEND\xaeB`\x82"
    )
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        response = client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("test.png", io.BytesIO(png_bytes), "image/png")},
        )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["work_item_id"] == item["id"]
    assert data["original_filename"] == "test.png"
    assert data["content_type"] == "image/png"
    assert data["file_size"] == len(png_bytes)
    assert data["id"] > 0


def test_upload_attachment_jpeg(client):
    """Upload a JPEG image attachment."""
    item = _create_work_item(client, title="JPEG test")
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        response = client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("photo.jpg", io.BytesIO(jpeg_bytes), "image/jpeg")},
        )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["content_type"] == "image/jpeg"
    assert data["original_filename"] == "photo.jpg"


def test_upload_attachment_rejects_invalid_type(client):
    """Reject non-allowed file types."""
    item = _create_work_item(client, title="Bad type test")
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        response = client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("malware.exe", io.BytesIO(b"MZ" + b"\x00" * 100), "application/x-executable")},
        )
    assert response.status_code == 415


def test_upload_attachment_rejects_empty_file(client):
    """Reject zero-byte files."""
    item = _create_work_item(client, title="Empty file test")
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        response = client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("empty.png", io.BytesIO(b""), "image/png")},
        )
    assert response.status_code == 400


def test_upload_attachment_rejects_oversized_file(client):
    """Reject files exceeding the size limit."""
    item = _create_work_item(client, title="Oversized test")
    big_bytes = b"\x00" * (11 * 1024 * 1024)  # 11 MB
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        response = client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("big.png", io.BytesIO(big_bytes), "image/png")},
        )
    assert response.status_code == 413


def test_list_attachments(client):
    """List attachments for a work item."""
    item = _create_work_item(client, title="List test")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00"
        b"IEND\xaeB`\x82"
    )
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("a.png", io.BytesIO(png_bytes), "image/png")},
        )
        client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("b.png", io.BytesIO(png_bytes), "image/png")},
        )

    response = client.get(f"/api/work-items/{item['id']}/attachments")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    filenames = [a["original_filename"] for a in data]
    assert "a.png" in filenames
    assert "b.png" in filenames


def test_list_attachments_empty(client):
    """Return empty list when no attachments exist."""
    item = _create_work_item(client, title="No attachments")
    response = client.get(f"/api/work-items/{item['id']}/attachments")
    assert response.status_code == 200
    assert response.json() == []


def test_download_attachment(client):
    """Download an attachment by ID."""
    item = _create_work_item(client, title="Download test")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00"
        b"IEND\xaeB`\x82"
    )
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        upload_resp = client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("download_me.png", io.BytesIO(png_bytes), "image/png")},
        )
    att_id = upload_resp.json()["id"]

    response = client.get(f"/api/work-items/{item['id']}/attachments/{att_id}")
    assert response.status_code == 200
    assert response.content == png_bytes


def test_download_attachment_not_found(client):
    """404 for non-existent attachment."""
    item = _create_work_item(client, title="Missing attachment")
    response = client.get(f"/api/work-items/{item['id']}/attachments/99999")
    assert response.status_code == 404


def test_download_attachment_wrong_work_item(client):
    """404 when attachment belongs to a different work item."""
    item1 = _create_work_item(client, title="Item 1")
    item2 = _create_work_item(client, title="Item 2")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00"
        b"IEND\xaeB`\x82"
    )
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        upload_resp = client.post(
            f"/api/work-items/{item1['id']}/attachments",
            files={"file": ("private.png", io.BytesIO(png_bytes), "image/png")},
        )
    att_id = upload_resp.json()["id"]

    # Try to access item1's attachment via item2's URL
    response = client.get(f"/api/work-items/{item2['id']}/attachments/{att_id}")
    assert response.status_code == 404


def test_delete_attachment(client):
    """Delete an attachment removes DB record and file."""
    item = _create_work_item(client, title="Delete test")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00"
        b"IEND\xaeB`\x82"
    )
    storage_dir = Path(tempfile.mkdtemp())
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", storage_dir):
        upload_resp = client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("delete_me.png", io.BytesIO(png_bytes), "image/png")},
        )
    att_id = upload_resp.json()["id"]
    files = list(storage_dir.iterdir())
    assert len(files) == 1
    storage_path = str(files[0])

    # Verify file exists
    assert os.path.exists(storage_path)

    # Delete
    response = client.delete(f"/api/work-items/{item['id']}/attachments/{att_id}")
    assert response.status_code == 204

    # Verify file removed
    assert not os.path.exists(storage_path)

    # Verify DB record gone
    list_resp = client.get(f"/api/work-items/{item['id']}/attachments")
    assert list_resp.json() == []


def test_delete_attachment_not_found(client):
    """404 when deleting non-existent attachment."""
    item = _create_work_item(client, title="Delete missing")
    response = client.delete(f"/api/work-items/{item['id']}/attachments/99999")
    assert response.status_code == 404


def test_upload_attachment_sanitizes_path_traversal(client):
    """Filename with path traversal is sanitized."""
    item = _create_work_item(client, title="Path traversal test")
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00"
        b"IEND\xaeB`\x82"
    )
    with patch("app.routers.work_items.ATTACHMENT_STORAGE_PATH", Path(tempfile.mkdtemp())):
        response = client.post(
            f"/api/work-items/{item['id']}/attachments",
            files={"file": ("../../../etc/passwd.png", io.BytesIO(png_bytes), "image/png")},
        )
    assert response.status_code == 201, response.text
    data = response.json()
    # Should be sanitized to just the filename portion
    assert data["original_filename"] == "passwd.png"


def test_work_item_creation_still_works(client):
    """Existing work item creation is not broken by attachment feature."""
    response = client.post(
        "/api/work-items",
        json={"type": "task", "title": "No attachments", "body": "Plain item"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "No attachments"
    assert data["id"] > 0
