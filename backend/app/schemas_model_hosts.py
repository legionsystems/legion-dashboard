"""Model Host schemas for UI-managed model selection."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Model Host schemas
# ---------------------------------------------------------------------------


class ModelHostResponse(BaseModel):
    """Response schema — excludes raw API key for security."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    base_url: str
    enabled: bool
    allow_cloud_endpoints: bool
    # API key never returned — only indicate if configured
    api_key_configured: bool
    # Status
    last_test_status: Optional[str] = None
    last_test_message: Optional[str] = None
    last_tested_at: Optional[datetime] = None
    last_models_refresh_at: Optional[datetime] = None
    last_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ModelHostCreate(BaseModel):
    """Create schema — API key is write-only."""
    name: str
    provider: str = "openai_compatible"
    base_url: str
    api_key: Optional[str] = None
    enabled: bool = True
    allow_cloud_endpoints: bool = False

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        if v.startswith(("javascript:", "file:")):
            raise ValueError("Unsupported URL scheme")
        return v


class ModelHostUpdate(BaseModel):
    """Update schema — API key is write-only."""
    name: Optional[str] = None
    provider: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    clear_api_key: bool = False
    enabled: Optional[bool] = None
    allow_cloud_endpoints: Optional[bool] = None


class ModelHostTestRequest(BaseModel):
    """Request for testing a model host connection."""
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout_seconds: int = 30


class ModelHostTestResponse(BaseModel):
    """Response from testing a model host."""
    success: bool
    host_name: str
    redacted_base_url: str
    latency_ms: Optional[float] = None
    error: Optional[str] = None
    message: Optional[str] = None


class ModelHostModelResponse(BaseModel):
    """Model catalog entry."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    host_id: int
    model_id: str
    display_name: Optional[str] = None
    is_available: bool
    discovered_at: datetime


# ---------------------------------------------------------------------------
# Extended Debate Execution schemas with host references
# ---------------------------------------------------------------------------


class DebateExecutionConfigResponseWithHosts(BaseModel):
    """Debate config response with host information."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    enabled: bool
    provider: str
    base_url: str
    model_mode: str
    default_model: str
    pro_model: Optional[str] = None
    con_model: Optional[str] = None
    arbiter_model: Optional[str] = None
    fallback_model: Optional[str] = None
    api_key_configured: bool
    timeout_seconds: int
    max_output_chars: int
    default_rounds: int
    allow_cloud_endpoints: bool
    notes: Optional[str] = None
    updated_at: datetime
