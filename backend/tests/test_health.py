"""Tests for the /health endpoint executor auth diagnostics (WI-26)."""

import os

from fastapi.testclient import TestClient

from app.main import _sha256_fingerprint, _executor_auth_diagnostics


def test_fingerprint_deterministic():
    """_sha256_fingerprint returns a stable 16-hex-char digest."""
    out = _sha256_fingerprint("hello")
    assert len(out) == 16
    assert _sha256_fingerprint("hello") == out
    assert _sha256_fingerprint("world") != out


def test_diagnostics_missing_key(monkeypatch):
    """When API_SERVER_KEY is unset, diagnostics report not configured."""
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    d = _executor_auth_diagnostics()
    assert d["executor_key_configured"] is False
    assert d["executor_key_fingerprint"] is None
    assert "MISSING" in d["executor_key_preview"]


def test_diagnostics_present_key(monkeypatch):
    """When API_SERVER_KEY is set, diagnostics report fingerprint only."""
    monkeypatch.setenv("API_SERVER_KEY", "test-key-abc123")
    d = _executor_auth_diagnostics()
    assert d["executor_key_configured"] is True
    assert d["executor_key_fingerprint"] == _sha256_fingerprint("test-key-abc123")
    assert d["executor_key_preview"] == "test-k..."
    # The full key must never appear in the preview.
    assert "test-key-abc123" not in d["executor_key_preview"]


def test_health_ok_with_key(client, monkeypatch):
    """Health returns status=ok when API_SERVER_KEY is set."""
    monkeypatch.setenv("API_SERVER_KEY", "some-key")
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["executor_auth"]["executor_key_configured"] is True


def test_health_degraded_without_key(client, monkeypatch):
    """Health returns status=degraded when API_SERVER_KEY is missing."""
    monkeypatch.delenv("API_SERVER_KEY", raising=False)
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["executor_auth"]["executor_key_configured"] is False
    assert "MISSING" in body["executor_auth"]["executor_key_preview"]
