"""Hermes model/provider discovery — read-only, secrets-safe.

Discovers model hosts and models from Hermes configuration without
modifying Hermes or exposing secrets.
"""
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yaml


def _redact_url(url: str) -> str:
    """Redact credentials from URL for safe logging/display."""
    try:
        parsed = urlparse(url)
        # Remove userinfo (username:password@)
        netloc = parsed.hostname or parsed.netloc
        return f"{parsed.scheme}://{netloc}{parsed.path}"
    except Exception:
        return url[:50] + "..." if len(url) > 50 else url


def _is_local_endpoint(url: str) -> bool:
    """Check if URL is a local/private endpoint."""
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        
        local_hosts = {"localhost", "127.0.0.1", "ai-4080", "legion", "ollama"}
        if hostname in local_hosts:
            return True
        # RFC1918
        if hostname.startswith("10.") or hostname.startswith("192.168."):
            return True
        if hostname.startswith("172."):
            parts = hostname.split(".")
            if len(parts) >= 2:
                second = int(parts[1])
                if 16 <= second <= 31:
                    return True
        # Tailscale
        if hostname.startswith("100."):
            parts = hostname.split(".")
            if len(parts) >= 2:
                second = int(parts[1])
                if 64 <= second <= 127:
                    return True
        return False
    except Exception:
        return False


def _find_hermes_home() -> Optional[Path]:
    """Find Hermes home directory using standard search order."""
    candidates = [
        Path(os.environ.get("HERMES_HOME", "")),
        Path.home() / ".hermes",
        Path("/root/.hermes"),
        Path("/srv/hermes"),
        Path("/app/.hermes"),  # Container-mounted Hermes config
    ]
    
    for candidate in candidates:
        if candidate and candidate.exists():
            config_path = candidate / "config.yaml"
            if config_path.exists():
                # Test if readable
                try:
                    with open(config_path, "r") as f:
                        f.read(1)
                    return candidate
                except (PermissionError, IOError):
                    continue
    
    # Check profiles
    profiles_base = Path("/srv/hermes/profiles")
    if profiles_base.exists():
        for profile_dir in profiles_base.iterdir():
            if profile_dir.is_dir() and (profile_dir / "config.yaml").exists():
                return profile_dir
    
    return None


def _load_hermes_config(hermes_home: Path) -> Optional[Dict[str, Any]]:
    """Load Hermes config.yaml safely."""
    config_path = hermes_home / "config.yaml"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _extract_provider_models(
    provider_name: str,
    provider_config: Dict[str, Any],
    profile_name: str = "default"
) -> List[Dict[str, Any]]:
    """Extract model host and models from a Hermes provider config."""
    hosts = []
    
    base_url = provider_config.get("base_url", "")
    if not base_url:
        return hosts
    
    models = provider_config.get("models", [])
    if not models:
        return hosts
    
    # Redact URL for safe storage
    redacted_url = _redact_url(base_url)
    
    host = {
        "name": f"{provider_name}",
        "provider": "openai_compatible",  # Hermes providers are typically OpenAI-compatible
        "base_url": redacted_url,
        "source": "hermes",
        "source_key": f"{profile_name}:{provider_name}",
        "profile_name": profile_name,
        "provider_name": provider_name,
        "sync_enabled": True,
        "allow_cloud_endpoints": _is_local_endpoint(base_url),
        "models": [],
    }
    
    for model in models:
        model_entry = {
            "model_id": model,
            "display_name": model,
            "source": "hermes",
            "source_key": f"{profile_name}:{provider_name}:{model}",
        }
        host["models"].append(model_entry)
    
    hosts.append(host)
    return hosts


def discover_from_hermes() -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Discover model hosts and models from Hermes configuration.
    
    Returns:
        Tuple of (list of host dicts, optional error message)
        
    Each host dict contains:
        - name: Display name
        - provider: Provider type (openai_compatible, etc.)
        - base_url: Redacted base URL
        - source: "hermes"
        - source_key: Stable identifier (profile:provider)
        - profile_name: Hermes profile name
        - provider_name: Hermes provider name
        - sync_enabled: True
        - allow_cloud_endpoints: Based on URL
        - models: List of model dicts
    """
    hermes_home = _find_hermes_home()
    if not hermes_home:
        return [], "Hermes configuration not found"
    
    config = _load_hermes_config(hermes_home)
    if not config:
        return [], "Failed to load Hermes config.yaml"
    
    hosts = []
    providers = config.get("providers", {})
    
    if not providers:
        return [], "No providers found in Hermes config"
    
    # Discover from default profile
    profile_name = "default"
    for provider_name, provider_config in providers.items():
        if not isinstance(provider_config, dict):
            continue
        
        discovered = _extract_provider_models(
            provider_name, provider_config, profile_name
        )
        hosts.extend(discovered)
    
    # Discover from named profiles
    profiles_path = hermes_home / "profiles"
    if profiles_path.exists():
        for profile_dir in profiles_path.iterdir():
            if not profile_dir.is_dir():
                continue
            
            profile_config_path = profile_dir / "config.yaml"
            if not profile_config_path.exists():
                continue
            
            try:
                with open(profile_config_path, "r", encoding="utf-8") as f:
                    profile_config = yaml.safe_load(f)
            except Exception:
                continue
            
            if not profile_config:
                continue
            
            profile_providers = profile_config.get("providers", {})
            for provider_name, provider_config in profile_providers.items():
                if not isinstance(provider_config, dict):
                    continue
                
                discovered = _extract_provider_models(
                    provider_name, provider_config, profile_dir.name
                )
                hosts.extend(discovered)
    
    if not hosts:
        return [], "No model providers with models found in Hermes config"
    
    return hosts, None


def dedupe_hosts(existing_hosts: List[Dict[str, Any]], new_hosts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate hosts by source_key or provider/base_url.
    
    - Manual hosts are never deleted
    - Hermes-synced hosts are updated/merged
    - Missing Hermes hosts are marked stale (not deleted)
    """
    # Index existing Hermes hosts by source_key
    hermes_by_key = {}
    for host in existing_hosts:
        if host.get("source") == "hermes" and host.get("source_key"):
            hermes_by_key[host["source_key"]] = host
    
    # Index new hosts by source_key
    new_by_key = {h["source_key"]: h for h in new_hosts if h.get("source_key")}
    
    result = []
    
    # Keep manual hosts
    for host in existing_hosts:
        if host.get("source") == "manual":
            result.append(host)
    
    # Update/add Hermes hosts
    for source_key, new_host in new_by_key.items():
        if source_key in hermes_by_key:
            # Update existing
            existing = hermes_by_key[source_key]
            existing["base_url"] = new_host["base_url"]
            existing["provider_name"] = new_host.get("provider_name")
            existing["profile_name"] = new_host.get("profile_name")
            existing["last_synced_at"] = datetime.utcnow()
            existing["last_sync_status"] = "success"
            existing["last_sync_error"] = None
            existing["models"] = new_host.get("models", [])
            result.append(existing)
        else:
            # New host
            new_host["last_synced_at"] = datetime.utcnow()
            new_host["last_sync_status"] = "success"
            new_host["last_sync_error"] = None
            result.append(new_host)
    
    # Mark missing Hermes hosts as stale
    for source_key, existing in hermes_by_key.items():
        if source_key not in new_by_key:
            existing["last_sync_status"] = "stale"
            existing["last_sync_error"] = "Provider no longer in Hermes config"
            existing["sync_enabled"] = False
            result.append(existing)
    
    return result
