"""Tests for debate execution settings API."""
import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal, Base
from app.models import DebateExecutionConfig


@pytest.fixture
def client():
    """Test client with isolated DB."""
    from app.database import engine
    from app.main import app

    # Create tables
    from app.models import DebateExecutionConfig
    Base.metadata.create_all(bind=engine)

    # Override DB for tests
    def override_get_db():
        db = SessionLocal()
        try:
            # Always reset to clean defaults for each test
            config = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()
            if config is None:
                config = DebateExecutionConfig(id=1)
                db.add(config)
            else:
                # Reset all fields to defaults
                config.enabled = False
                config.provider = "openai_compatible"
                config.base_url = "http://ai-4080:11434/v1"
                config.model = "deepseek-r1:32b"
                config.api_key = None
                config.timeout_seconds = 180
                config.max_output_chars = 12000
                config.default_rounds = 2
                config.allow_cloud_endpoints = False
                config.notes = None
            db.commit()
            db.refresh(config)
            yield db
        finally:
            db.close()

    from app import main
    main.get_db = override_get_db

    with TestClient(app) as c:
        yield c


class TestDebateExecutionSettings:
    """Test debate execution configuration endpoints."""

    def test_get_settings_excludes_api_key(self, client):
        """GET /api/settings/debate-execution never returns raw API key."""
        # Set a test API key via API
        payload = {"api_key": "sk-test-secret-key-12345", "enabled": True}
        client.put("/api/settings/debate-execution", json=payload)

        response = client.get("/api/settings/debate-execution")
        assert response.status_code == 200
        data = response.json()

        # API key should NOT be present
        assert "api_key" not in data
        # Instead, we get a boolean flag
        assert "api_key_configured" in data
        assert data["api_key_configured"] is True
        assert data["enabled"] is True

    def test_get_settings_default_values(self, client):
        """GET returns default values when not configured."""
        # Reset via API - explicit defaults
        payload = {
            "enabled": False,
            "clear_api_key": True,
            "base_url": "http://ai-4080:11434/v1",
            "provider": "openai_compatible",
            "model_mode": "single_model",
            "default_model": "deepseek-r1:32b",
            "timeout_seconds": 180,
            "max_output_chars": 12000,
            "default_rounds": 2,
            "allow_cloud_endpoints": False,
        }
        client.put("/api/settings/debate-execution", json=payload)

        response = client.get("/api/settings/debate-execution")
        assert response.status_code == 200
        data = response.json()

        assert data["enabled"] is False
        assert data["api_key_configured"] is False
        assert data["provider"] == "openai_compatible"
        assert data["model_mode"] == "single_model"
        assert data["default_model"] == "deepseek-r1:32b"
        assert data["base_url"] == "http://ai-4080:11434/v1"
        assert data["default_rounds"] == 2
        assert data["allow_cloud_endpoints"] is False

    def test_update_settings_persists(self, client):
        """PUT /api/settings/debate-execution persists non-secret config."""
        payload = {
            "enabled": True,
            "provider": "openai_compatible",
            "base_url": "http://ai-4080:11434/v1",
            "model": "deepseek-r1:32b",
            "timeout_seconds": 300,
            "max_output_chars": 15000,
            "default_rounds": 3,
        }
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["enabled"] is True
        assert data["timeout_seconds"] == 300
        assert data["max_output_chars"] == 15000
        assert data["default_rounds"] == 3

        # Verify persistence
        db = SessionLocal()
        config = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()
        assert config.enabled is True
        assert config.timeout_seconds == 300
        db.close()

    def test_set_api_key_not_returned(self, client):
        """API key can be set but is never returned."""
        payload = {
            "api_key": "sk-new-secret-key-67890",
        }
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 200
        data = response.json()

        # API key should NOT be in response
        assert "api_key" not in data
        assert data["api_key_configured"] is True

        # But it IS stored
        db = SessionLocal()
        config = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()
        assert config.api_key == "sk-new-secret-key-67890"
        db.close()

    def test_clear_api_key(self, client):
        """API key can be cleared."""
        # First set a key
        db = SessionLocal()
        config = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()
        config.api_key = "sk-to-be-cleared"
        db.commit()
        db.close()

        # Clear it
        payload = {"clear_api_key": True}
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert data["api_key_configured"] is False

        # Verify cleared
        db = SessionLocal()
        config = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()
        assert config.api_key is None
        db.close()

    def test_cloud_endpoint_blocked_without_flag(self, client):
        """Public cloud endpoints require allow_cloud_endpoints=true."""
        payload = {
            "base_url": "https://api.openai.com/v1",
        }
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 400
        assert "allow_cloud_endpoints" in response.json()["detail"]

    def test_cloud_endpoint_allowed_with_flag(self, client):
        """Cloud endpoints allowed when flag is enabled."""
        # First enable the flag
        payload = {"allow_cloud_endpoints": True}
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 200

        # Now set cloud endpoint
        payload = {
            "base_url": "https://api.openai.com/v1",
            "allow_cloud_endpoints": True,
        }
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 200

    def test_local_endpoint_always_allowed(self, client):
        """Local/private endpoints allowed without cloud flag."""
        local_urls = [
            "http://localhost:11434/v1",
            "http://127.0.0.1:11434/v1",
            "http://ai-4080:11434/v1",
            "http://192.168.1.100:11434/v1",
            "http://10.0.0.5:11434/v1",
            "http://100.112.27.64:11434/v1",  # Tailscale
        ]
        for url in local_urls:
            payload = {"base_url": url}
            response = client.put("/api/settings/debate-execution", json=payload)
            assert response.status_code == 200, f"Failed for {url}"

    def test_invalid_url_scheme_rejected(self, client):
        """Invalid URL schemes are rejected."""
        invalid_urls = [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "ftp://example.com",
            "not-a-url",
        ]
        for url in invalid_urls:
            payload = {"base_url": url}
            response = client.put("/api/settings/debate-execution", json=payload)
            assert response.status_code == 400, f"Should have failed for {url}"

    def test_rounds_validation(self, client):
        """default_rounds must be 1-5."""
        # Too low
        payload = {"default_rounds": 0}
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 422

        # Too high
        payload = {"default_rounds": 6}
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 422

        # Valid
        for rounds in [1, 2, 3, 4, 5]:
            payload = {"default_rounds": rounds}
            response = client.put("/api/settings/debate-execution", json=payload)
            assert response.status_code == 200

    def test_timeout_validation(self, client):
        """timeout_seconds must be 10-600."""
        # Too low
        payload = {"timeout_seconds": 5}
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 422

        # Too high
        payload = {"timeout_seconds": 1000}
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 422

        # Valid
        for timeout in [10, 180, 600]:
            payload = {"timeout_seconds": timeout}
            response = client.put("/api/settings/debate-execution", json=payload)
            assert response.status_code == 200

    def test_max_output_chars_validation(self, client):
        """max_output_chars must be 1000-100000."""
        # Too low
        payload = {"max_output_chars": 500}
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 422

        # Too high
        payload = {"max_output_chars": 200000}
        response = client.put("/api/settings/debate-execution", json=payload)
        assert response.status_code == 422

        # Valid
        for chars in [1000, 12000, 100000]:
            payload = {"max_output_chars": chars}
            response = client.put("/api/settings/debate-execution", json=payload)
            assert response.status_code == 200

    def test_test_connection_success(self, client):
        """Test connection endpoint works (mocked)."""
        # This will fail to connect but should return safe error
        payload = {
            "base_url": "http://localhost:9999/v1",
            "model": "test-model",
        }
        response = client.post("/api/settings/debate-execution/test", json=payload)
        assert response.status_code == 200
        data = response.json()

        assert "success" in data
        assert "provider" in data
        assert "base_url_host" in data
        assert "error" in data
        # API key never in response
        assert "api_key" not in data

    def test_test_connection_redacts_cloud(self, client):
        """Test connection redacts cloud provider hosts."""
        payload = {
            "base_url": "https://api.openai.com/v1",
            "allow_cloud_endpoints": True,
        }
        # First allow cloud
        client.put("/api/settings/debate-execution", json={"allow_cloud_endpoints": True})

        response = client.post("/api/settings/debate-execution/test", json=payload)
        assert response.status_code == 200
        data = response.json()

        # Host should be redacted
        assert "cloud-provider" in data["base_url_host"] or "***" in data["base_url_host"]
