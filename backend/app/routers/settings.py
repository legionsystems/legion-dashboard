"""Debate execution settings router — UI-managed configuration."""
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DebateExecutionConfig
from ..schemas import (
    DebateExecutionConfigResponse,
    DebateExecutionConfigUpdate,
    DebateExecutionTestRequest,
    DebateExecutionTestResponse,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _get_config(db: Session) -> DebateExecutionConfig:
    """Get or create singleton config row."""
    config = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()
    if config is None:
        # Create default config
        config = DebateExecutionConfig(id=1)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _redact_url_host(url: str) -> str:
    """Extract and redact URL host for safe display."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "unknown"
        # Redact if looks like public cloud
        public_clouds = ["openai.com", "anthropic.com", "api.openai.com"]
        if any(c in host.lower() for c in public_clouds):
            return "***.cloud-provider"
        return host
    except Exception:
        return "invalid-url"


def _is_local_endpoint(url: str) -> bool:
    """Check if URL is a local/private endpoint."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()

        # Local/private patterns
        local_patterns = [
            "localhost",
            "127.0.0.1",
            "0.0.0.0",
            "ai-4080",
            "lgn-remote",
            "lgn-local",
        ]
        if any(p in host for p in local_patterns):
            return True

        # RFC1918 private ranges (simplified check)
        if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172."):
            return True

        # Tailscale pattern (100.x.y.z)
        if host.startswith("100."):
            return True

        return False
    except Exception:
        return False


@router.get("/debate-execution", response_model=DebateExecutionConfigResponse)
def get_debate_execution_config(db: Session = Depends(get_db)):
    """Get debate execution configuration.

    API key is NEVER returned — only api_key_configured boolean.
    """
    config = _get_config(db)
    return DebateExecutionConfigResponse(
        id=config.id,
        enabled=config.enabled,
        provider=config.provider,
        base_url=config.base_url,
        model_mode=config.model_mode,
        default_model=config.default_model,
        pro_model=config.pro_model,
        con_model=config.con_model,
        arbiter_model=config.arbiter_model,
        fallback_model=config.fallback_model,
        api_key_configured=config.api_key is not None and len(config.api_key) > 0,
        timeout_seconds=config.timeout_seconds,
        max_output_chars=config.max_output_chars,
        default_rounds=config.default_rounds,
        allow_cloud_endpoints=config.allow_cloud_endpoints,
        notes=config.notes,
        updated_at=config.updated_at,
    )


@router.put("/debate-execution", response_model=DebateExecutionConfigResponse)
def update_debate_execution_config(
    payload: DebateExecutionConfigUpdate,
    db: Session = Depends(get_db),
):
    """Update debate execution configuration.

    API key is write-only — never returned in response.
    Use clear_api_key=true to remove stored key.
    """
    config = _get_config(db)

    # Validate cloud endpoint guard
    if payload.base_url:
        # Check if new URL is cloud endpoint
        is_cloud = not _is_local_endpoint(payload.base_url)
        # Check if cloud is allowed (either already enabled OR being enabled in this request)
        cloud_allowed = config.allow_cloud_endpoints or (payload.allow_cloud_endpoints is True)

        if is_cloud and not cloud_allowed:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Cloud/public endpoints require allow_cloud_endpoints=true. "
                    "Local/private endpoints (localhost, 127.0.0.1, ai-4080, "
                    "192.168.x.x, 10.x.x.x, Tailscale) are allowed by default."
                ),
            )

    # Apply updates
    if payload.enabled is not None:
        config.enabled = payload.enabled
    if payload.provider is not None:
        config.provider = payload.provider
    if payload.base_url is not None:
        # Validate URL scheme
        parsed = urlparse(payload.base_url)
        if parsed.scheme not in ("http", "https"):
            raise HTTPException(
                status_code=400,
                detail="base_url must use http:// or https:// scheme",
            )
        config.base_url = payload.base_url
    if payload.model_mode is not None:
        config.model_mode = payload.model_mode
    if payload.default_model is not None:
        config.default_model = payload.default_model
    if payload.pro_model is not None:
        config.pro_model = payload.pro_model
    if payload.con_model is not None:
        config.con_model = payload.con_model
    if payload.arbiter_model is not None:
        config.arbiter_model = payload.arbiter_model
    if payload.fallback_model is not None:
        config.fallback_model = payload.fallback_model

    # API key handling
    if payload.clear_api_key:
        config.api_key = None
    elif payload.api_key is not None:
        config.api_key = payload.api_key

    if payload.timeout_seconds is not None:
        config.timeout_seconds = payload.timeout_seconds
    if payload.max_output_chars is not None:
        config.max_output_chars = payload.max_output_chars
    if payload.default_rounds is not None:
        config.default_rounds = payload.default_rounds
    if payload.allow_cloud_endpoints is not None:
        config.allow_cloud_endpoints = payload.allow_cloud_endpoints
    if payload.notes is not None:
        config.notes = payload.notes

    config.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(config)

    return DebateExecutionConfigResponse(
        id=config.id,
        enabled=config.enabled,
        provider=config.provider,
        base_url=config.base_url,
        model_mode=config.model_mode,
        default_model=config.default_model,
        pro_model=config.pro_model,
        con_model=config.con_model,
        arbiter_model=config.arbiter_model,
        fallback_model=config.fallback_model,
        api_key_configured=config.api_key is not None and len(config.api_key) > 0,
        timeout_seconds=config.timeout_seconds,
        max_output_chars=config.max_output_chars,
        default_rounds=config.default_rounds,
        allow_cloud_endpoints=config.allow_cloud_endpoints,
        notes=config.notes,
        updated_at=config.updated_at,
    )


@router.post("/debate-execution/test", response_model=DebateExecutionTestResponse)
def test_debate_execution_connection(
    payload: DebateExecutionTestRequest,
    db: Session = Depends(get_db),
):
    """Test connection to configured (or supplied) endpoint.

    Does not persist settings. Returns safe result without secrets.
    """
    import time

    import httpx

    config = _get_config(db)

    # Use supplied values or fall back to saved config
    base_url = payload.base_url or config.base_url
    model = payload.model or config.default_model
    api_key = payload.api_key or config.api_key
    timeout = payload.timeout_seconds or config.timeout_seconds

    # Validate URL
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        return DebateExecutionTestResponse(
            success=False,
            provider=config.provider,
            base_url_host="invalid-url",
            model=model,
            error="base_url must use http:// or https:// scheme",
        )

    # Build test request
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    test_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Respond with only: OK"},
            {"role": "user", "content": "Test"},
        ],
        "max_tokens": 10,
    }

    try:
        start = time.time()
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, headers=headers, json=test_payload)
            response.raise_for_status()
        latency_ms = int((time.time() - start) * 1000)

        return DebateExecutionTestResponse(
            success=True,
            provider=config.provider,
            base_url_host=_redact_url_host(base_url),
            model=model,
            latency_ms=latency_ms,
        )
    except httpx.TimeoutException:
        return DebateExecutionTestResponse(
            success=False,
            provider=config.provider,
            base_url_host=_redact_url_host(base_url),
            model=model,
            error="Connection timeout",
        )
    except httpx.ConnectError as e:
        return DebateExecutionTestResponse(
            success=False,
            provider=config.provider,
            base_url_host=_redact_url_host(base_url),
            model=model,
            error=f"Connection failed: {str(e)[:100]}",
        )
    except httpx.HTTPStatusError as e:
        return DebateExecutionTestResponse(
            success=False,
            provider=config.provider,
            base_url_host=_redact_url_host(base_url),
            model=model,
            error=f"HTTP {e.response.status_code}: {str(e)[:100]}",
        )
    except Exception as e:
        return DebateExecutionTestResponse(
            success=False,
            provider=config.provider,
            base_url_host=_redact_url_host(base_url),
            model=model,
            error=f"Unexpected error: {type(e).__name__}",
        )
