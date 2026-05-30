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
from ..hermes_sync import dedupe_hosts, discover_from_hermes
from ..models import ModelHost, ModelHostModel, ModelSyncRun
from ..schemas_model_hosts import (
    ModelHostCreate,
    ModelHostModelResponse,
    ModelHostResponse,
    ModelHostTestRequest,
    ModelHostTestResponse,
    ModelHostUpdate,
    ModelSyncRunResponse,
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


@router.get("/sync-runs", response_model=List[ModelSyncRunResponse])
def list_sync_runs(db: Session = Depends(get_db)):
    """List Hermes sync run history."""
    runs = db.query(ModelSyncRun).order_by(ModelSyncRun.created_at.desc()).limit(20).all()
    return runs


@router.post("/sync-hermes", response_model=ModelSyncRunResponse)
def sync_from_hermes(db: Session = Depends(get_db)):
    """Sync model hosts and models from Hermes configuration.
    
    Discovers providers/models from Hermes config files (read-only).
    Updates local cache, marks stale entries, preserves manual hosts.
    Never exposes API keys or secrets.
    """
    # Create sync run record
    sync_run = ModelSyncRun(
        source="hermes",
        status="running",
    )
    db.add(sync_run)
    db.commit()
    db.refresh(sync_run)
    
    try:
        # Discover from Hermes
        discovered_hosts, error = discover_from_hermes()
        
        if error:
            sync_run.status = "completed"
            sync_run.error_message = error
            sync_run.hosts_discovered = 0
            sync_run.models_discovered = 0
            sync_run.completed_at = datetime.utcnow()
            db.commit()
            
            return ModelSyncRunResponse(
                id=sync_run.id,
                source=sync_run.source,
                status=sync_run.status,
                hosts_discovered=0,
                models_discovered=0,
                error_message=error,
                created_at=sync_run.created_at,
                completed_at=sync_run.completed_at,
            )
        
        # Get existing hosts
        existing_hosts = db.query(ModelHost).all()
        existing_list = []
        for h in existing_hosts:
            host_dict = {
                "id": h.id,
                "name": h.name,
                "provider": h.provider,
                "base_url": h.base_url,
                "source": h.source,
                "source_key": h.source_key,
                "profile_name": h.profile_name,
                "provider_name": h.provider_name,
                "sync_enabled": h.sync_enabled,
                "allow_cloud_endpoints": h.allow_cloud_endpoints,
                "models": [{"model_id": m.model_id, "display_name": m.display_name} for m in h.models],
            }
            existing_list.append(host_dict)
        
        # Dedupe and merge
        merged_hosts = dedupe_hosts(existing_list, discovered_hosts)
        
        # Apply changes to DB
        hosts_added = 0
        models_added = 0
        
        # Index existing by source_key
        existing_by_key = {h.source_key: h for h in existing_hosts if h.source_key}
        existing_by_name = {h.name: h for h in existing_hosts}
        
        for host_data in merged_hosts:
            if host_data.get("source") != "hermes":
                continue
            
            source_key = host_data.get("source_key")
            name = host_data.get("name")
            
            # Find or create host
            host = None
            if source_key and source_key in existing_by_key:
                host = existing_by_key[source_key]
            elif name in existing_by_name:
                host = existing_by_name[name]
            
            if not host:
                # Create new
                host = ModelHost(
                    name=name,
                    provider=host_data.get("provider", "openai_compatible"),
                    base_url=host_data.get("base_url", ""),
                    source="hermes",
                    source_key=source_key,
                    profile_name=host_data.get("profile_name"),
                    provider_name=host_data.get("provider_name"),
                    sync_enabled=host_data.get("sync_enabled", True),
                    allow_cloud_endpoints=host_data.get("allow_cloud_endpoints", False),
                )
                db.add(host)
                db.commit()
                db.refresh(host)
                hosts_added += 1
            else:
                # Update existing
                host.base_url = host_data.get("base_url", host.base_url)
                host.provider_name = host_data.get("provider_name")
                host.profile_name = host_data.get("profile_name")
                host.last_synced_at = datetime.utcnow()
                host.last_sync_status = host_data.get("last_sync_status", "success")
                host.last_sync_error = host_data.get("last_sync_error")
                if host_data.get("sync_enabled") is not None:
                    host.sync_enabled = host_data["sync_enabled"]
                db.commit()
            
            # Sync models
            models_data = host_data.get("models", [])
            models_count = 0
            
            # Clear existing Hermes-synced models for this host
            db.query(ModelHostModel).filter(
                ModelHostModel.host_id == host.id,
                ModelHostModel.source == "hermes"
            ).delete()
            
            for model_data in models_data:
                model = ModelHostModel(
                    host_id=host.id,
                    model_id=model_data.get("model_id", ""),
                    display_name=model_data.get("display_name"),
                    source="hermes",
                    source_key=model_data.get("source_key"),
                    last_synced_at=datetime.utcnow(),
                )
                db.add(model)
                models_count += 1
            
            models_added += models_count
            db.commit()
        
        # Update sync run
        sync_run.status = "completed"
        sync_run.hosts_discovered = hosts_added
        sync_run.models_discovered = models_added
        sync_run.completed_at = datetime.utcnow()
        db.commit()
        
        return ModelSyncRunResponse(
            id=sync_run.id,
            source=sync_run.source,
            status=sync_run.status,
            hosts_discovered=hosts_added,
            models_discovered=models_added,
            error_message=None,
            created_at=sync_run.created_at,
            completed_at=sync_run.completed_at,
        )
        
    except Exception as e:
        error_msg = str(e)[:500]
        sync_run.status = "failed"
        sync_run.error_message = error_msg
        sync_run.completed_at = datetime.utcnow()
        db.commit()
        
        raise HTTPException(status_code=500, detail=f"Sync failed: {error_msg}")


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
        source="manual",  # Explicitly mark as manual
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
