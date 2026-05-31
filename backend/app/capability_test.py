"""Test model host capabilities.

Checks which APIs are available for a given provider.
"""
import json
import time
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from ..models import ModelHost
from ..schemas import CapabilityCheckResult, ModelHostCapabilityTestResponse


def test_host_capabilities(host: ModelHost, db: Session, selected_model: Optional[str] = None) -> ModelHostCapabilityTestResponse:
    """Test all capability endpoints for a model host.
    
    Returns structured result showing which APIs work.
    """
    checks = []
    recommended_api = None
    selected_model_available = None
    
    # 1. DNS/connectivity check
    try:
        start = time.time()
        with httpx.Client(timeout=5) as client:
            # Try to connect to base URL
            response = client.get(host.base_url.rstrip('/') + '/')
        latency = int((time.time() - start) * 1000)
        checks.append(CapabilityCheckResult(
            name="dns_connectivity",
            status="success",
            latency_ms=latency,
            message=f"Connected in {latency}ms"
        ))
    except Exception as e:
        checks.append(CapabilityCheckResult(
            name="dns_connectivity",
            status="failed",
            message=f"Connection failed: {str(e)[:100]}"
        ))
        # Can't continue if can't connect
        return ModelHostCapabilityTestResponse(
            provider_id=host.id,
            provider_type=host.provider_type,
            enabled=host.enabled,
            overall_status="failed",
            checks=checks,
            safe_error="Cannot connect to provider"
        )
    
    base_url = host.base_url.rstrip('/')
    headers = {}
    if host.api_key:
        headers["Authorization"] = f"Bearer {host.api_key}"
    
    # 2. Ollama native API tests (if provider_type suggests Ollama)
    ollama_native_works = False
    if host.provider_type in ("ollama_native", "openai_compatible"):
        # Try /api/tags
        try:
            start = time.time()
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{base_url}/api/tags", headers=headers)
                response.raise_for_status()
                data = response.json()
                models_count = len(data.get("models", []))
            latency = int((time.time() - start) * 1000)
            checks.append(CapabilityCheckResult(
                name="ollama_api_tags",
                status="success",
                endpoint="/api/tags",
                latency_ms=latency,
                message=f"Found {models_count} models"
            ))
            ollama_native_works = True
        except httpx.HTTPStatusError as e:
            checks.append(CapabilityCheckResult(
                name="ollama_api_tags",
                status="failed",
                endpoint="/api/tags",
                message=f"HTTP {e.response.status_code}"
            ))
        except Exception as e:
            checks.append(CapabilityCheckResult(
                name="ollama_api_tags",
                status="failed",
                endpoint="/api/tags",
                message=f"{type(e).__name__}: {str(e)[:80]}"
            ))
        
        # Try /api/chat (generation endpoint)
        try:
            start = time.time()
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    f"{base_url}/api/chat",
                    headers=headers,
                    json={
                        "model": selected_model or "test-model",
                        "messages": [{"role": "user", "content": "OK"}],
                        "stream": False,
                    }
                )
                response.raise_for_status()
            latency = int((time.time() - start) * 1000)
            checks.append(CapabilityCheckResult(
                name="ollama_api_chat",
                status="success",
                endpoint="/api/chat",
                latency_ms=latency,
                message="Generation works"
            ))
            ollama_native_works = True
            recommended_api = "ollama_native"
        except httpx.HTTPStatusError as e:
            checks.append(CapabilityCheckResult(
                name="ollama_api_chat",
                status="failed",
                endpoint="/api/chat",
                message=f"HTTP {e.response.status_code}"
            ))
        except Exception as e:
            checks.append(CapabilityCheckResult(
                name="ollama_api_chat",
                status="failed",
                endpoint="/api/chat",
                message=f"{type(e).__name__}: {str(e)[:80]}"
            ))
        
        # Try /api/ps (loaded models)
        try:
            start = time.time()
            with httpx.Client(timeout=5) as client:
                response = client.get(f"{base_url}/api/ps", headers=headers)
                response.raise_for_status()
            latency = int((time.time() - start) * 1000)
            checks.append(CapabilityCheckResult(
                name="ollama_api_ps",
                status="success",
                endpoint="/api/ps",
                latency_ms=latency,
                message="Loaded models endpoint works"
            ))
        except:
            checks.append(CapabilityCheckResult(
                name="ollama_api_ps",
                status="skipped",
                endpoint="/api/ps",
                message="Not available"
            ))
    
    # 3. OpenAI-compatible API tests
    openai_works = False
    if host.provider_type in ("openai_compatible", "xai"):
        # Try /v1/models
        try:
            start = time.time()
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{base_url}/v1/models", headers=headers)
                response.raise_for_status()
                data = response.json()
                models_count = len(data.get("data", []))
            latency = int((time.time() - start) * 1000)
            checks.append(CapabilityCheckResult(
                name="openai_models",
                status="success",
                endpoint="/v1/models",
                latency_ms=latency,
                message=f"Found {models_count} models"
            ))
            openai_works = True
        except httpx.HTTPStatusError as e:
            checks.append(CapabilityCheckResult(
                name="openai_models",
                status="failed",
                endpoint="/v1/models",
                message=f"HTTP {e.response.status_code}"
            ))
        except Exception as e:
            checks.append(CapabilityCheckResult(
                name="openai_models",
                status="failed",
                endpoint="/v1/models",
                message=f"{type(e).__name__}: {str(e)[:80]}"
            ))
        
        # Try /v1/chat/completions (generation endpoint)
        try:
            start = time.time()
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    f"{base_url}/v1/chat/completions",
                    headers=headers,
                    json={
                        "model": selected_model or "test-model",
                        "messages": [{"role": "user", "content": "OK"}],
                        "max_tokens": 10,
                    }
                )
                response.raise_for_status()
            latency = int((time.time() - start) * 1000)
            checks.append(CapabilityCheckResult(
                name="openai_chat_completions",
                status="success",
                endpoint="/v1/chat/completions",
                latency_ms=latency,
                message="Generation works"
            ))
            openai_works = True
            if not recommended_api:
                recommended_api = "openai_chat_completions"
        except httpx.HTTPStatusError as e:
            checks.append(CapabilityCheckResult(
                name="openai_chat_completions",
                status="failed",
                endpoint="/v1/chat/completions",
                message=f"HTTP {e.response.status_code}"
            ))
        except Exception as e:
            checks.append(CapabilityCheckResult(
                name="openai_chat_completions",
                status="failed",
                endpoint="/v1/chat/completions",
                message=f"{type(e).__name__}: {str(e)[:80]}"
            ))
    
    # 4. Selected model availability check
    if selected_model:
        try:
            # Try to get model info
            model_found = False
            # Check Ollama style
            try:
                with httpx.Client(timeout=5) as client:
                    response = client.get(f"{base_url}/api/tags", headers=headers)
                    if response.status_code == 200:
                        data = response.json()
                        models = [m.get("name", "") for m in data.get("models", [])]
                        model_found = any(selected_model in m for m in models)
            except:
                pass
            
            # Check OpenAI style
            if not model_found:
                try:
                    with httpx.Client(timeout=5) as client:
                        response = client.get(f"{base_url}/v1/models", headers=headers)
                        if response.status_code == 200:
                            data = response.json()
                            models = [m.get("id", "") for m in data.get("data", [])]
                            model_found = any(selected_model == m or selected_model in m for m in models)
                except:
                    pass
            
            selected_model_available = model_found
            checks.append(CapabilityCheckResult(
                name="selected_model_available",
                status="success" if model_found else "failed",
                message=f"Model '{selected_model}' {'found' if model_found else 'not found'}"
            ))
        except Exception as e:
            checks.append(CapabilityCheckResult(
                name="selected_model_available",
                status="failed",
                message=f"Could not check: {str(e)[:80]}"
            ))
    
    # Determine overall status
    if ollama_native_works or openai_works:
        overall_status = "success"
        if not ollama_native_works and not openai_works:
            overall_status = "warning"
    else:
        overall_status = "failed"
    
    # Update host with capability results
    host.supports_native_ollama = ollama_native_works
    host.supports_openai_chat_completions = openai_works
    host.supports_model_list = any(c.name in ("ollama_api_tags", "openai_models") and c.status == "success" for c in checks)
    host.supports_loaded_models = any(c.name == "ollama_api_ps" and c.status == "success" for c in checks)
    host.preferred_generation_api = recommended_api
    host.last_capability_result = json.dumps({
        "checks": [c.model_dump() for c in checks],
        "recommended_generation_api": recommended_api,
        "selected_model_available": selected_model_available,
    })
    host.last_tested_at = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    host.last_test_status = overall_status
    
    db.commit()
    
    return ModelHostCapabilityTestResponse(
        provider_id=host.id,
        provider_type=host.provider_type,
        enabled=host.enabled,
        overall_status=overall_status,
        checks=checks,
        recommended_generation_api=recommended_api,
        selected_model_available=selected_model_available,
        selected_model=selected_model,
    )
