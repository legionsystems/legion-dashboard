"""Model Hosts router — UI-managed model provider configuration."""
import json
import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import ModelHost, ModelHostModel
from ..schemas_model_hosts import (
    ModelHostCreate,
    ModelHostModelResponse,
    ModelHostResponse,
    ModelHostTestRequest,
    ModelHostTestResponse,
    ModelHostUpdate,
)


router = APIRouter(prefix="/api/settings/model-hosts", tags=["settings"])


def _is_local_endpoint(url: str) -> bool:
    """Check if URL is a local/private endpoint."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        # Local/private hostnames
        local_hosts = {
            "localhost",
            "127.0.0.1",
            "ai-4080",
            "LEGION",
            "ollama",
        }
        if hostname.lower() in local_hosts:
            return True
        # RFC1918 private ranges
        if hostname.startswith("10.") or hostname.startswith("192.168."):
            return True
        if hostname.startswith("172."):
            parts = hostname.split(".")
            if len(parts) >= 2:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return True
        # Tailscale (100.64.0.0/10)
        if hostname.startswith("100."):
            parts = hostname.split(".")
            if len(parts) >= 2:
                second = int(parts[1])
                if 64 <= second <= 127:
                    return True
        return False
    except Exception:
        return False


def _redact_base_url(url: str) -> str:
    """Redact credentials from URL for safe display."""
    try:
        parsed = urlparse(url)
        # Remove any embedded credentials
        netloc = parsed.hostname or parsed.netloc
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    except Exception:
        return url[:50] + "..." if len(url) > 50 else url


@router.get("", response_model=List[ModelHostResponse])
def list_model_hosts(db: Session = Depends(get_db)):
    """List all configured model hosts."""
    hosts = db.query(ModelHost).order_by(ModelHost.name).all()
    return hosts


@router.post("", response_model=ModelHostResponse)
def create_model_host(payload: ModelHostCreate, db: Session = Depends(get_db)):
    """Create a new model host."""
    # Check for duplicate name
    existing = db.query(ModelHost).filter(ModelHost.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Host name already exists")

    # Validate cloud endpoint guard
    if not _is_local_endpoint(payload.base_url) and not payload.allow_cloud_endpoints:
        raise HTTPException(
            status_code=400,
            detail="Cloud endpoints require 'allow_cloud_endpoints' to be enabled"
        )

    host = ModelHost(
        name=payload.name,
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


@router.get("/{host_id}", response_model=ModelHostResponse)
def get_model_host(host_id: int, db: Session = Depends(get_db)):
    """Get a specific model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    return host


@router.put("/{host_id}", response_model=ModelHostResponse)
def update_model_host(
    host_id: int,
    payload: ModelHostUpdate,
    db: Session = Depends(get_db)
):
    """Update a model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Apply updates
    if payload.name is not None:
        # Check for duplicate name (excluding self)
        existing = db.query(ModelHost).filter(
            ModelHost.name == payload.name,
            ModelHost.id != host_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Host name already exists")
        host.name = payload.name

    if payload.provider is not None:
        host.provider = payload.provider

    if payload.base_url is not None:
        # Validate cloud endpoint guard
        if not _is_local_endpoint(payload.base_url) and not payload.allow_cloud_endpoints:
            # Check if current host already allows cloud
            if not host.allow_cloud_endpoints:
                raise HTTPException(
                    status_code=400,
                    detail="Cloud endpoints require 'allow_cloud_endpoints' to be enabled"
                )
        host.base_url = payload.base_url

    if payload.api_key is not None:
        host.api_key = payload.api_key

    if payload.clear_api_key:
        host.api_key = None

    if payload.enabled is not None:
        host.enabled = payload.enabled

    if payload.allow_cloud_endpoints is not None:
        host.allow_cloud_endpoints = payload.allow_cloud_endpoints

    host.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(host)
    return host


@router.delete("/{host_id}")
def delete_model_host(host_id: int, db: Session = Depends(get_db)):
    """Delete a model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    db.delete(host)
    db.commit()
    return {"status": "deleted", "host_id": host_id}


@router.post("/{host_id}/test", response_model=ModelHostTestResponse)
def test_model_host(
    host_id: int,
    payload: Optional[ModelHostTestRequest] = None,
    db: Session = Depends(get_db)
):
    """Test connection to a model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    # Use supplied values or fall back to saved config
    base_url = payload.base_url if payload and payload.base_url else host.base_url
    api_key = payload.api_key if payload and payload.api_key else host.api_key
    timeout = payload.timeout_seconds if payload else 30

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        start = time.time()
        # Test with a simple /models request
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()

        latency_ms = (time.time() - start) * 1000

        # Update host status
        host.last_test_status = "success"
        host.last_test_message = "Connection successful"
        host.last_tested_at = datetime.utcnow()
        host.last_error = None
        db.commit()

        return ModelHostTestResponse(
            success=True,
            host_name=host.name,
            redacted_base_url=_redact_base_url(base_url),
            latency_ms=round(latency_ms, 2),
            message="Connection successful",
        )

    except httpx.TimeoutException as e:
        error_msg = f"Timeout after {timeout}s"
        host.last_test_status = "failed"
        host.last_test_message = error_msg
        host.last_tested_at = datetime.utcnow()
        host.last_error = error_msg
        db.commit()

        return ModelHostTestResponse(
            success=False,
            host_name=host.name,
            redacted_base_url=_redact_base_url(base_url),
            error=error_msg,
        )

    except Exception as e:
        error_msg = str(e)
        host.last_test_status = "failed"
        host.last_test_message = error_msg[:500]
        host.last_tested_at = datetime.utcnow()
        host.last_error = error_msg[:500]
        db.commit()

        return ModelHostTestResponse(
            success=False,
            host_name=host.name,
            redacted_base_url=_redact_base_url(base_url),
            error=error_msg[:200],
        )


@router.post("/{host_id}/refresh-models")
def refresh_model_hosts_models(host_id: int, db: Session = Depends(get_db)):
    """Refresh available models from a model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    try:
        headers = {"Content-Type": "application/json"}
        if host.api_key:
            headers["Authorization"] = f"Bearer {host.api_key}"

        with httpx.Client(timeout=60) as client:
            response = client.get(f"{host.base_url}/models", headers=headers)
            response.raise_for_status()
            data = response.json()

        # Parse models from OpenAI-compatible response
        # Format: {"data": [{"id": "model-id", ...}, ...]}
        models_data = data.get("data", [])

        # Clear existing models
        db.query(ModelHostModel).filter(ModelHostModel.host_id == host_id).delete()

        # Add discovered models
        for model_entry in models_data:
            model_id = model_entry.get("id", "")
            if not model_id:
                continue

            # Bound raw JSON to prevent oversized storage
            raw_json = json.dumps(model_entry, ensure_ascii=False)[:10000]

            model = ModelHostModel(
                host_id=host_id,
                model_id=model_id,
                display_name=model_entry.get("name") or model_entry.get("display_name") or model_id,
                raw_json=raw_json,
                is_available=model_entry.get("is_available", True),
            )
            db.add(model)

        host.last_models_refresh_at = datetime.utcnow()
        host.last_error = None
        db.commit()

        return {
            "status": "refreshed",
            "host_id": host_id,
            "models_count": len(models_data),
        }

    except Exception as e:
        error_msg = str(e)[:500]
        host.last_error = error_msg
        db.commit()

        raise HTTPException(status_code=500, detail=f"Failed to refresh models: {error_msg}")


@router.get("/{host_id}/models", response_model=List[ModelHostModelResponse])
def list_host_models(host_id: int, db: Session = Depends(get_db)):
    """List available models for a model host."""
    host = db.query(ModelHost).filter(ModelHost.id == host_id).first()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    models = db.query(ModelHostModel).filter(
        ModelHostModel.host_id == host_id
    ).order_by(ModelHostModel.model_id).all()

    return models
