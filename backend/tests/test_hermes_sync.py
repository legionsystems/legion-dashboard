"""Tests for Hermes model sync functionality."""
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock

from app.hermes_sync import (
    _redact_url,
    _is_local_endpoint,
    _find_hermes_home,
    _extract_provider_models,
    discover_from_hermes,
    dedupe_hosts,
)


class TestUrlRedaction:
    def test_redact_url_with_credentials(self):
        """Redact embedded credentials from URL."""
        url = "https://user:pass@api.openai.com/v1"
        result = _redact_url(url)
        assert "user" not in result
        assert "pass" not in result
        assert "api.openai.com" in result

    def test_redact_url_plain(self):
        """Plain URL - port is stripped (hostname only for safety)."""
        url = "http://ai-4080:11434/v1"
        result = _redact_url(url)
        # Port is stripped by urlparse when reconstructing
        assert "ai-4080" in result
        assert result.startswith("http://")


class TestLocalEndpointDetection:
    def test_localhost(self):
        assert _is_local_endpoint("http://localhost:11434/v1") is True

    def test_127_0_0_1(self):
        assert _is_local_endpoint("http://127.0.0.1:11434/v1") is True

    def test_ai_4080(self):
        assert _is_local_endpoint("http://ai-4080:11434/v1") is True

    def test_rfc1918_10(self):
        assert _is_local_endpoint("http://10.0.0.1:11434/v1") is True

    def test_rfc1918_192_168(self):
        assert _is_local_endpoint("http://192.168.1.1:11434/v1") is True

    def test_tailscale(self):
        assert _is_local_endpoint("http://100.64.0.1:11434/v1") is True

    def test_cloud_endpoint(self):
        assert _is_local_endpoint("https://api.openai.com/v1") is False


class TestExtractProviderModels:
    def test_extract_basic(self):
        """Extract host and models from provider config."""
        config = {
            "base_url": "http://ai-4080:11434/v1",
            "models": ["deepseek-r1:32b", "llama3:8b"],
        }
        hosts = _extract_provider_models("test-provider", config, "default")
        
        assert len(hosts) == 1
        host = hosts[0]
        assert host["name"] == "test-provider"
        assert host["source"] == "hermes"
        assert host["source_key"] == "default:test-provider"
        assert host["provider_name"] == "test-provider"
        assert host["profile_name"] == "default"
        assert len(host["models"]) == 2
        assert host["models"][0]["model_id"] == "deepseek-r1:32b"

    def test_no_base_url(self):
        """Skip providers without base_url."""
        config = {"models": ["model1"]}
        hosts = _extract_provider_models("test", config)
        assert len(hosts) == 0

    def test_no_models(self):
        """Skip providers without models."""
        config = {"base_url": "http://example.com"}
        hosts = _extract_provider_models("test", config)
        assert len(hosts) == 0


class TestDedupeHosts:
    def test_keep_manual_hosts(self):
        """Manual hosts are preserved."""
        existing = [{
            "id": 1, "name": "manual-host", "source": "manual",
            "base_url": "http://manual.com", "models": [],
        }]
        new = [{
            "name": "hermes-host", "source": "hermes", "source_key": "default:prov",
            "base_url": "http://hermes.com", "models": [],
        }]
        
        result = dedupe_hosts(existing, new)
        
        # Manual host preserved
        manual_hosts = [h for h in result if h["source"] == "manual"]
        assert len(manual_hosts) == 1
        assert manual_hosts[0]["name"] == "manual-host"

    def test_update_existing_hermes_host(self):
        """Existing Hermes hosts get updated."""
        existing = [{
            "id": 1, "name": "prov", "source": "hermes", "source_key": "default:prov",
            "base_url": "http://old.com", "models": [],
        }]
        new = [{
            "name": "prov", "source": "hermes", "source_key": "default:prov",
            "base_url": "http://new.com", "models": [{"model_id": "m1"}],
        }]
        
        result = dedupe_hosts(existing, new)
        
        hermes_hosts = [h for h in result if h["source"] == "hermes"]
        assert len(hermes_hosts) == 1
        assert hermes_hosts[0]["base_url"] == "http://new.com"
        assert len(hermes_hosts[0]["models"]) == 1

    def test_mark_stale_hermes_hosts(self):
        """Hermes hosts missing from new sync are marked stale."""
        existing = [{
            "id": 1, "name": "old-prov", "source": "hermes", "source_key": "default:old",
            "base_url": "http://old.com", "models": [],
        }]
        new = []  # No hosts in new sync
        
        result = dedupe_hosts(existing, new)
        
        hermes_hosts = [h for h in result if h["source"] == "hermes"]
        assert len(hermes_hosts) == 1
        assert hermes_hosts[0]["last_sync_status"] == "stale"
        assert hermes_hosts[0]["sync_enabled"] is False


class TestDiscoverFromHermes:
    @patch("app.hermes_sync._find_hermes_home")
    @patch("app.hermes_sync._load_hermes_config")
    def test_discover_success(self, mock_load, mock_find):
        """Successful discovery from Hermes config."""
        mock_find.return_value = MagicMock()
        mock_load.return_value = {
            "providers": {
                "test-provider": {
                    "base_url": "http://test:11434/v1",
                    "models": ["model1", "model2"],
                }
            }
        }
        
        hosts, error = discover_from_hermes()
        
        assert error is None
        assert len(hosts) == 1
        assert hosts[0]["name"] == "test-provider"
        assert len(hosts[0]["models"]) == 2

    @patch("app.hermes_sync._find_hermes_home")
    def test_no_hermes_home(self, mock_find):
        """Graceful handling when Hermes not found."""
        mock_find.return_value = None
        
        hosts, error = discover_from_hermes()
        
        assert error == "Hermes configuration not found"
        assert len(hosts) == 0

    @patch("app.hermes_sync._find_hermes_home")
    @patch("app.hermes_sync._load_hermes_config")
    def test_no_providers(self, mock_load, mock_find):
        """Graceful handling when no providers configured."""
        mock_find.return_value = MagicMock()
        mock_load.return_value = {"providers": {}}
        
        hosts, error = discover_from_hermes()
        
        assert error == "No providers found in Hermes config"
        assert len(hosts) == 0
