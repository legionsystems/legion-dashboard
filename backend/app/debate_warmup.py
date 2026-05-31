"""Model warmup and debate cleanup implementation for LEGION Dashboard."""

import httpx
from datetime import datetime
from typing import Optional, Tuple
from sqlalchemy.orm import Session

from .models import DebateExecutionConfig, DebateRun, ModelHost


def warm_model_ollama_native(
    base_url: str,
    model: str,
    keep_alive: str = "1h",
    timeout_seconds: int = 300,
) -> Tuple[bool, Optional[str], int]:
    """Warm up a model using Ollama native API.
    
    Uses POST {base}/api/chat with empty messages to load model into memory.
    Includes keep_alive parameter to keep model resident.
    
    Args:
        base_url: Ollama base URL (e.g., http://ai-4080:11434)
        model: Model name to warm
        keep_alive: How long to keep model loaded (e.g., "1h", "30m")
        timeout_seconds: Request timeout
        
    Returns:
        Tuple of (success, error_message, latency_ms)
    """
    from .debate_executor import derive_ollama_native_url
    from urllib.parse import urlparse
    
    # Derive native URL
    native_url = derive_ollama_native_url(base_url)
    
    # Build warmup request - NO work item data, just empty messages
    payload = {
        "model": model,
        "messages": [],  # Empty messages - just to load model
        "keep_alive": keep_alive,
        "stream": False,
    }
    
    start_time = datetime.utcnow()
    
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(native_url, json=payload)
            response.raise_for_status()
        
        latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        return True, None, latency_ms
        
    except httpx.TimeoutException:
        return False, f"Model warmup timed out after {timeout_seconds}s", 0
    except httpx.ConnectError as e:
        return False, f"Failed to connect to model endpoint: {str(e)[:200]}", 0
    except httpx.HTTPStatusError as e:
        return False, f"Model endpoint returned HTTP {e.response.status_code}", 0
    except Exception as e:
        return False, f"Unexpected warmup error: {type(e).__name__}", 0


def warm_model_openai_compatible(
    base_url: str,
    model: str,
    api_key: Optional[str] = None,
    timeout_seconds: int = 300,
) -> Tuple[bool, Optional[str], int]:
    """Warm up a model using OpenAI-compatible API.
    
    Uses a tiny chat completion request to load model.
    Does NOT include any work item data.
    
    Args:
        base_url: OpenAI-compatible base URL (e.g., http://ai-4080:11434/v1)
        model: Model name to warm
        api_key: Optional API key
        timeout_seconds: Request timeout
        
    Returns:
        Tuple of (success, error_message, latency_ms)
    """
    url = base_url.rstrip("/") + "/chat/completions"
    
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    # Tiny generic prompt - NO work item data
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Respond with only: OK"},
            {"role": "user", "content": "Ready?"},
        ],
        "max_tokens": 10,
        "stream": False,
    }
    
    start_time = datetime.utcnow()
    
    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        
        latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        return True, None, latency_ms
        
    except httpx.TimeoutException:
        return False, f"Model warmup timed out after {timeout_seconds}s", 0
    except httpx.ConnectError as e:
        return False, f"Failed to connect to model endpoint: {str(e)[:200]}", 0
    except httpx.HTTPStatusError as e:
        return False, f"Model endpoint returned HTTP {e.response.status_code}", 0
    except Exception as e:
        return False, f"Unexpected warmup error: {type(e).__name__}", 0


def warm_model_if_enabled(
    db: Session,
    run: DebateRun,
    config: DebateExecutionConfig,
) -> Tuple[bool, Optional[str], str, int]:
    """Warm up the model before debate execution if enabled.
    
    Args:
        db: Database session
        run: Debate run to update
        config: Debate execution config
        
    Returns:
        Tuple of (success, error_message, warmup_method, latency_ms)
    """
    # Check if warmup is enabled
    if not config.warm_model_before_debate:
        return True, None, "disabled", 0
    
    # Resolve base URL and model
    base_url = config.base_url
    model = config.default_model
    api_key = config.api_key
    
    # Resolve host if selected
    if config.default_host_id:
        host = db.query(ModelHost).filter(ModelHost.id == config.default_host_id).first()
        if host and host.enabled:
            base_url = host.base_url
            api_key = host.api_key or api_key
    
    # Check cloud endpoint guard
    from .debate_executor import is_local_endpoint
    is_cloud = not is_local_endpoint(base_url)
    if is_cloud and not config.allow_cloud_endpoints:
        return False, "Cloud endpoints not allowed - enable allow_cloud_endpoints in settings", "cloud_blocked", 0
    
    # Determine warmup method based on URL pattern
    # Ollama native: URLs ending with /v1 or containing ollama
    is_ollama = base_url.rstrip("/").endswith("/v1") or "ollama" in base_url.lower()
    
    # Update run state
    run.execution_stage = "warming"
    run.warmup_started_at = datetime.utcnow()
    run.warmup_method = "ollama_native" if is_ollama else "openai_compatible_ping"
    
    # Perform warmup
    if is_ollama:
        # Use Ollama native API
        success, error, latency_ms = warm_model_ollama_native(
            base_url=base_url,
            model=model,
            keep_alive=config.keep_model_loaded_for,
            timeout_seconds=config.warmup_timeout_seconds,
        )
    else:
        # Use OpenAI-compatible ping
        success, error, latency_ms = warm_model_openai_compatible(
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=config.warmup_timeout_seconds,
        )
    
    # Record results
    run.warmup_completed_at = datetime.utcnow()
    run.warmup_duration_ms = latency_ms
    
    if success:
        return True, None, run.warmup_method, latency_ms
    else:
        run.warmup_error = error
        return False, error, run.warmup_method, latency_ms
