"""Debate execution settings router — UI-managed configuration."""
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DebateExecutionConfig, ModelHost
from ..schemas import (
    DebateExecutionConfigResponse,
    DebateExecutionConfigUpdate,
    DebateExecutionTestRequest,
    DebateExecutionTestResponse,
    ModelWarmupRequest,
    ModelWarmupResponse,
    ModelHostResponse,
    ModelHostCreate,
    ModelHostUpdate,
    ModelHostCapabilityTestResponse,
    CapabilityCheckResult,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/model-hosts", response_model=List[ModelHostResponse])
def list_model_hosts(db: Session = Depends(get_db)):
    """List all model hosts with their models.
    
    Disabled hosts are included but marked. API keys are never returned.
    """
    hosts = db.query(ModelHost).order_by(ModelHost.name).all()
    return hosts


@router.get("/model-hosts/{host_id}", response_model=ModelHostResponse)
def get_model_host(host_id: int, db: Session = Depends(get_db)):
    """Get a specific model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if host is None:
        raise HTTPException(status_code=404, detail="Model host not found")
    return host


@router.post("/model-hosts", response_model=ModelHostResponse, status_code=status.HTTP_201_CREATED)
def create_model_host(payload: ModelHostCreate, db: Session = Depends(get_db)):
    """Create a new model host."""
    # Check for duplicate name
    existing = db.query(ModelHost).filter(ModelHost.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Model host '{payload.name}' already exists")
    
    host = ModelHost(
        name=payload.name,
        provider_type=payload.provider_type,
        provider=payload.provider,
        base_url=payload.base_url,
        api_key=payload.api_key,
        enabled=payload.enabled,
        allow_cloud_endpoints=payload.allow_cloud_endpoints,
    )
    db.add(host)
    db.commit()
    db.refresh(host)
    return host


@router.put("/model-hosts/{host_id}", response_model=ModelHostResponse)
def update_model_host(host_id: int, payload: ModelHostUpdate, db: Session = Depends(get_db)):
    """Update a model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if host is None:
        raise HTTPException(status_code=404, detail="Model host not found")
    
    if payload.name is not None:
        # Check for duplicate name (excluding self)
        existing = db.query(ModelHost).filter(
            ModelHost.name == payload.name,
            ModelHost.id != host_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Model host '{payload.name}' already exists")
        host.name = payload.name
    
    if payload.provider_type is not None:
        host.provider_type = payload.provider_type
    if payload.provider is not None:
        host.provider = payload.provider
    if payload.base_url is not None:
        host.base_url = payload.base_url
    if payload.api_key is not None:
        host.api_key = payload.api_key
    if payload.clear_api_key:
        host.api_key = None
    if payload.enabled is not None:
        host.enabled = payload.enabled
    if payload.allow_cloud_endpoints is not None:
        host.allow_cloud_endpoints = payload.allow_cloud_endpoints
    if payload.supports_native_ollama is not None:
        host.supports_native_ollama = payload.supports_native_ollama
    if payload.supports_openai_chat_completions is not None:
        host.supports_openai_chat_completions = payload.supports_openai_chat_completions
    if payload.supports_model_list is not None:
        host.supports_model_list = payload.supports_model_list
    if payload.supports_loaded_models is not None:
        host.supports_loaded_models = payload.supports_loaded_models
    if payload.preferred_generation_api is not None:
        host.preferred_generation_api = payload.preferred_generation_api
    
    db.commit()
    db.refresh(host)
    return host


@router.delete("/model-hosts/{host_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model_host(host_id: int, db: Session = Depends(get_db)):
    """Delete a model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if host is None:
        raise HTTPException(status_code=404, detail="Model host not found")
    
    db.delete(host)
    db.commit()
    return None


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
        default_host_id=config.default_host_id,
        default_model=config.default_model,
        pro_host_id=config.pro_host_id,
        pro_model=config.pro_model,
        con_host_id=config.con_host_id,
        con_model=config.con_model,
        arbiter_host_id=config.arbiter_host_id,
        arbiter_model=config.arbiter_model,
        fallback_host_id=config.fallback_host_id,
        fallback_model=config.fallback_model,
        api_key_configured=bool(config.api_key),
        timeout_seconds=config.timeout_seconds,
        max_output_chars=config.max_output_chars,
        default_rounds=config.default_rounds,
        allow_cloud_endpoints=config.allow_cloud_endpoints,
        warm_model_before_debate=config.warm_model_before_debate,
        warmup_timeout_seconds=config.warmup_timeout_seconds,
        keep_model_loaded_for=config.keep_model_loaded_for,
        fail_debate_if_warmup_fails=config.fail_debate_if_warmup_fails,
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
    if payload.default_host_id is not None:
        config.default_host_id = payload.default_host_id
    if payload.default_model is not None:
        config.default_model = payload.default_model
    if payload.pro_host_id is not None:
        config.pro_host_id = payload.pro_host_id
    if payload.pro_model is not None:
        config.pro_model = payload.pro_model
    if payload.con_host_id is not None:
        config.con_host_id = payload.con_host_id
    if payload.con_model is not None:
        config.con_model = payload.con_model
    if payload.arbiter_host_id is not None:
        config.arbiter_host_id = payload.arbiter_host_id
    if payload.arbiter_model is not None:
        config.arbiter_model = payload.arbiter_model
    if payload.fallback_host_id is not None:
        config.fallback_host_id = payload.fallback_host_id
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
    if payload.warm_model_before_debate is not None:
        config.warm_model_before_debate = payload.warm_model_before_debate
    if payload.warmup_timeout_seconds is not None:
        config.warmup_timeout_seconds = payload.warmup_timeout_seconds
    if payload.keep_model_loaded_for is not None:
        config.keep_model_loaded_for = payload.keep_model_loaded_for
    if payload.fail_debate_if_warmup_fails is not None:
        config.fail_debate_if_warmup_fails = payload.fail_debate_if_warmup_fails
    if payload.visible_failed_runs_limit is not None:
        config.visible_failed_runs_limit = payload.visible_failed_runs_limit
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
        default_host_id=config.default_host_id,
        default_model=config.default_model,
        pro_host_id=config.pro_host_id,
        pro_model=config.pro_model,
        con_host_id=config.con_host_id,
        con_model=config.con_model,
        arbiter_host_id=config.arbiter_host_id,
        arbiter_model=config.arbiter_model,
        fallback_host_id=config.fallback_host_id,
        fallback_model=config.fallback_model,
        api_key_configured=bool(config.api_key),
        timeout_seconds=config.timeout_seconds,
        max_output_chars=config.max_output_chars,
        default_rounds=config.default_rounds,
        allow_cloud_endpoints=config.allow_cloud_endpoints,
        warm_model_before_debate=bool(config.warm_model_before_debate),
        warmup_timeout_seconds=int(config.warmup_timeout_seconds),
        keep_model_loaded_for=str(config.keep_model_loaded_for),
        fail_debate_if_warmup_fails=bool(config.fail_debate_if_warmup_fails),
        visible_failed_runs_limit=int(config.visible_failed_runs_limit),
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


# ---------------------------------------------------------------------------
# Model warmup endpoints
# ---------------------------------------------------------------------------


def _derive_ollama_native_url(base_url: str) -> str:
    """Derive native Ollama API URL from OpenAI-compatible base.
    
    http://ai-4080:11434/v1 -> http://ai-4080:11434/api/chat
    """
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        return url[:-3] + "/api/chat"
    return url


@router.post("/model-hosts/{host_id}/test-capability", response_model=ModelHostCapabilityTestResponse)
def test_model_host_capability(
    host_id: int,
    selected_model: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Test all capabilities of a model host.
    
    Returns structured result showing which APIs work:
    - Ollama native (/api/tags, /api/chat, /api/ps)
    - OpenAI-compatible (/v1/models, /v1/chat/completions)
    - Selected model availability
    
    Updates host capability flags in database.
    """
    from ..capability_test import test_host_capabilities
    
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if host is None:
        raise HTTPException(status_code=404, detail="Model host not found")
    
    return test_host_capabilities(host, db, selected_model)
def warm_model(
    host_id: int,
    model_id: str,
    payload: ModelWarmupRequest,
    db: Session = Depends(get_db),
):
    """Warm up a specific model on a model host.
    
    For Ollama endpoints, uses native /api/chat with empty messages.
    For other OpenAI-compatible endpoints, uses a minimal completion ping.
    """
    import httpx
    
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if host is None:
        raise HTTPException(status_code=404, detail="Model host not found")
    
    if not host.enabled:
        return ModelWarmupResponse(
            success=False,
            model=model_id,
            warmup_method="skipped",
            error="Host is disabled",
        )
    
    # Cloud endpoint guard
    is_cloud = not _is_local_endpoint(host.base_url)
    if is_cloud and not host.allow_cloud_endpoints:
        return ModelWarmupResponse(
            success=False,
            model=model_id,
            warmup_method="skipped",
            error="Cloud endpoints require allow_cloud_endpoints=true",
        )
    
    base_url = host.base_url
    timeout = payload.timeout_seconds or 300
    keep_alive = payload.keep_alive or "1h"
    
    # Derive native Ollama URL if applicable
    is_ollama_native = "ollama" in host.provider.lower() or "11434" in base_url
    
    try:
        start = time.time()
        
        if is_ollama_native:
            # Native Ollama warmup: POST /api/chat with empty messages
            warmup_url = _derive_ollama_native_url(base_url)
            warmup_payload = {
                "model": model_id,
                "messages": [],
                "keep_alive": keep_alive,
            }
            headers = {"Content-Type": "application/json"}
            if host.api_key:
                headers["Authorization"] = f"Bearer {host.api_key}"
            
            with httpx.Client(timeout=timeout) as client:
                response = client.post(warmup_url, headers=headers, json=warmup_payload)
                response.raise_for_status()
            
            warmup_method = "ollama_native"
        else:
            # OpenAI-compatible ping
            warmup_url = base_url.rstrip("/") + "/chat/completions"
            warmup_payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": "OK"}],
                "max_tokens": 1,
            }
            headers = {"Content-Type": "application/json"}
            if host.api_key:
                headers["Authorization"] = f"Bearer {host.api_key}"
            
            with httpx.Client(timeout=timeout) as client:
                response = client.post(warmup_url, headers=headers, json=warmup_payload)
                response.raise_for_status()
            
            warmup_method = "openai_compatible_ping"
        
        latency_ms = int((time.time() - start) * 1000)
        
        return ModelWarmupResponse(
            success=True,
            provider=host.provider,
            base_url_host=_redact_url_host(base_url),
            model=model_id,
            warmup_method=warmup_method,
            latency_ms=latency_ms,
        )
        
    except httpx.TimeoutException:
        return ModelWarmupResponse(
            success=False,
            provider=host.provider,
            base_url_host=_redact_url_host(base_url),
            model=model_id,
            warmup_method="ollama_native" if is_ollama_native else "openai_compatible_ping",
            error="Warmup timeout",
        )
    except httpx.ConnectError as e:
        return ModelWarmupResponse(
            success=False,
            provider=host.provider,
            base_url_host=_redact_url_host(base_url),
            model=model_id,
            warmup_method="ollama_native" if is_ollama_native else "openai_compatible_ping",
            error=f"Connection failed: {str(e)[:100]}",
        )
    except httpx.HTTPStatusError as e:
        return ModelWarmupResponse(
            success=False,
            provider=host.provider,
            base_url_host=_redact_url_host(base_url),
            model=model_id,
            warmup_method="ollama_native" if is_ollama_native else "openai_compatible_ping",
            error=f"HTTP {e.response.status_code}: {str(e)[:100]}",
        )
    except Exception as e:
        return ModelWarmupResponse(
            success=False,
            provider=host.provider,
            base_url_host=_redact_url_host(base_url),
            model=model_id,
            warmup_method="ollama_native" if is_ollama_native else "openai_compatible_ping",
            error=f"Unexpected error: {type(e).__name__}",
        )


@router.get("/model-hosts/{host_id}/loaded-models", response_model=List[str])
def get_loaded_models(
    host_id: int,
    db: Session = Depends(get_db),
):
    """Get currently loaded models on an Ollama host.
    
    Calls GET /api/ps on Ollama native endpoints.
    Returns empty list for non-Ollama endpoints or on error.
    """
    import httpx
    
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if host is None:
        raise HTTPException(status_code=404, detail="Model host not found")
    
    if not host.enabled:
        return []
    
    # Only works for Ollama native
    is_ollama = "ollama" in host.provider.lower() or "11434" in host.base_url
    if not is_ollama:
        return []
    
    # Derive base URL (strip /v1 if present)
    base_url = host.base_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    
    ps_url = base_url + "/api/ps"
    
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(ps_url)
            response.raise_for_status()
            data = response.json()
        
        # Ollama /api/ps returns {"models": [{"name": "...", ...}, ...]}
        models = data.get("models", [])
        return [m.get("name", "") for m in models if m.get("name")]
    except Exception:
        return []
