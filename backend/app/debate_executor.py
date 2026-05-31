"""Debate execution bridge — turns queued DebateRuns into real debate content.

This module provides a minimal, safe execution bridge for debate runs.
It uses a configurable OpenAI-compatible endpoint (default: local Ollama).

Environment variables (all optional):
    DEBATE_EXECUTION_ENABLED: "true" to enable execution (default: "false")
    DEBATE_PROVIDER: Provider name for provenance (default: "openai_compatible")
    DEBATE_BASE_URL: API base URL (default: "http://ai-4080:11434/v1")
    DEBATE_MODEL: Model name (default: "deepseek-r1:32b")
    DEBATE_API_KEY: API key (optional, not required for local Ollama)
    DEBATE_TIMEOUT_SECONDS: Request timeout (default: 180)
    DEBATE_MAX_OUTPUT_CHARS: Max chars per argument (default: 12000)

Safety rules:
    - Execution is OFF by default
    - No secrets logged
    - Bounded output
    - Graceful error handling
    - Advisory only (no work item mutation)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from .models import DebateArgument, DebateExecutionConfig, DebateRun, OperatorDebateInput, WorkItem
from .debate_warmup import warm_model_ollama_native, warm_model_openai_compatible


# Debate roles in order
DEBATE_ROLES = [
    "Product Owner",
    "UX/Design Reviewer",
    "Technical Architect",
    "Security/Privacy Reviewer",
    "Builder",
    "Skeptic/Red Team",
]

# Final arbiter recommendation values
RECOMMENDATIONS = [
    "APPROVE_AS_IS",
    "APPROVE_WITH_EDITS",
    "SPLIT_FIRST",
    "NEEDS_MORE_DETAIL",
    "DO_NOT_BUILD_NOW",
]

READINESS_STATES = ["READY", "READY_AFTER_EDITS", "NOT_READY"]


@dataclass
class ExecutionConfig:
    """Debate execution configuration from environment."""

    enabled: bool = False
    provider: str = "openai_compatible"
    base_url: str = "http://ai-4080:11434/v1"
    model: str = "deepseek-r1:32b"
    api_key: Optional[str] = None
    timeout_seconds: int = 180
    max_output_chars: int = 12000

    @classmethod
    def from_env(cls) -> "ExecutionConfig":
        enabled = os.environ.get("DEBATE_EXECUTION_ENABLED", "").lower() == "true"
        api_key = os.environ.get("DEBATE_API_KEY", "").strip() or None
        return cls(
            enabled=enabled,
            provider=os.environ.get("DEBATE_PROVIDER", "openai_compatible"),
            base_url=os.environ.get("DEBATE_BASE_URL", "http://ai-4080:11434/v1"),
            model=os.environ.get("DEBATE_MODEL", "deepseek-r1:32b"),
            api_key=api_key,
            timeout_seconds=int(os.environ.get("DEBATE_TIMEOUT_SECONDS", "180")),
            max_output_chars=int(os.environ.get("DEBATE_MAX_OUTPUT_CHARS", "12000")),
        )

    def redacted_base_url(self) -> str:
        """Return base URL with credentials redacted for logging."""
        url = self.base_url
        # Redact password if present (http://user:pass@host)
        if "@" in url:
            url = re.sub(r"://[^@]+@", "://***@", url)
        return url


def get_execution_config(db: Session) -> ExecutionConfig:
    """Load execution config from DB settings (preferred) or environment (fallback).

    DB settings take precedence. Environment variables are only used as
    bootstrap defaults when no DB row exists.

    Resolves ModelHost references to actual base_url/provider/api_key.
    """
    # Try DB first
    config_row = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()

    if config_row is not None:
        # Resolve host if selected
        base_url = config_row.base_url
        provider = config_row.provider
        api_key = config_row.api_key
        
        # If host_id is set, use the host's settings (overrides base_url/provider/api_key)
        if config_row.default_host_id:
            from .models import ModelHost
            host = db.query(ModelHost).filter(ModelHost.id == config_row.default_host_id).first()
            if host and host.enabled:
                base_url = host.base_url
                # Use provider_type for routing, fall back to legacy provider
                provider = host.provider_type if host.provider_type else host.provider
                api_key = host.api_key or api_key

        # Use default_model for single-model mode, or fall back to it for compatibility
        model = config_row.default_model

        return ExecutionConfig(
            enabled=config_row.enabled,
            provider=provider,
            base_url=base_url,
            model=model,
            api_key=api_key,
            timeout_seconds=config_row.timeout_seconds,
            max_output_chars=config_row.max_output_chars,
        )

    # Fallback to environment (bootstrap only)
    return ExecutionConfig.from_env()


def derive_ollama_native_url(openai_compatible_url: str) -> str:
    """Derive Ollama native API URL from OpenAI-compatible URL.
    
    Example: http://ai-4080:11434/v1 -> http://ai-4080:11434/api/chat
    
    Args:
        openai_compatible_url: URL ending with /v1
        
    Returns:
        Native Ollama chat endpoint URL
    """
    from urllib.parse import urlparse, urlunparse
    
    parsed = urlparse(openai_compatible_url)
    
    # Strip /v1 suffix if present
    path = parsed.path
    if path.endswith("/v1"):
        path = path[:-3]  # Remove /v1
    elif path.endswith("/v1/"):
        path = path[:-4]  # Remove /v1/
    
    # Build native chat endpoint
    native_path = path.rstrip("/") + "/api/chat"
    
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        native_path,
        parsed.params,
        parsed.query,
        parsed.fragment
    ))


def is_local_endpoint(url: str) -> bool:
    """Check if URL is a local/private endpoint (not cloud)."""
    from urllib.parse import urlparse
    
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
        
        # RFC1918 private ranges
        if host.startswith("192.168.") or host.startswith("10.") or host.startswith("172."):
            return True
        
        # Tailscale pattern (100.x.y.z)
        if host.startswith("100."):
            return True
        
        return False
    except Exception:
        return False


def warm_model_if_enabled(
    db_session: Session,
    config_row: DebateExecutionConfig,
    model: str,
) -> dict:
    """Warm up the model before debate execution if enabled.
    
    Args:
        db_session: Database session
        config_row: Debate execution config with warmup settings
        model: Model name to warm
        
    Returns:
        dict with keys:
            - success: bool
            - warmup_method: str (ollama_native | openai_compatible_ping | skipped)
            - duration_ms: int (if successful)
            - error: str (if failed)
    """
    # Check if warmup is enabled
    if not config_row.warm_model_before_debate:
        return {"success": True, "warmup_method": "skipped", "duration_ms": 0}
    
    # Resolve host if selected
    base_url = config_row.base_url
    api_key = config_row.api_key
    
    if config_row.default_host_id:
        from .models import ModelHost
        host = db_session.query(ModelHost).filter(ModelHost.id == config_row.default_host_id).first()
        if host and host.enabled:
            base_url = host.base_url
            api_key = host.api_key or api_key
            print(f"[WARMUP] Using host {host.name}: base_url={base_url}, provider_type={host.provider_type}")
    
    print(f"[WARMUP] Resolved base_url: {base_url}")
    
    # Cloud endpoint guard
    is_cloud = not is_local_endpoint(base_url)
    if is_cloud and not config_row.allow_cloud_endpoints:
        return {
            "success": False,
            "warmup_method": "skipped",
            "error": "Cloud endpoints require allow_cloud_endpoints=true",
        }
    
    # Derive native Ollama URL if applicable
    is_ollama = "ollama" in base_url.lower() or "11434" in base_url
    warmup_url = derive_ollama_native_url(base_url) if is_ollama else base_url.rstrip("/") + "/chat/completions"
    
    print(f"[WARMUP] is_ollama={is_ollama}, warmup_url={warmup_url}")
    
    timeout = config_row.warmup_timeout_seconds
    keep_alive = config_row.keep_model_loaded_for
    
    try:
        start = time.time()
        
        if is_ollama:
            # Native Ollama warmup: POST /api/chat with empty messages
            warmup_payload = {
                "model": model,
                "messages": [],
                "keep_alive": keep_alive,
            }
            warmup_method = "ollama_native"
        else:
            # OpenAI-compatible ping
            warmup_payload = {
                "model": model,
                "messages": [{"role": "user", "content": "OK"}],
                "max_tokens": 1,
            }
            warmup_method = "openai_compatible_ping"
        
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        with httpx.Client(timeout=timeout) as client:
            response = client.post(warmup_url, headers=headers, json=warmup_payload)
            response.raise_for_status()
        
        duration_ms = int((time.time() - start) * 1000)
        
        return {
            "success": True,
            "warmup_method": warmup_method,
            "duration_ms": duration_ms,
        }
        
    except httpx.TimeoutException:
        return {
            "success": False,
            "warmup_method": "ollama_native" if is_ollama else "openai_compatible_ping",
            "error": f"Warmup timeout after {timeout}s",
        }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "warmup_method": "ollama_native" if is_ollama else "openai_compatible_ping",
            "error": f"Connection failed: {str(e)[:200]}",
        }
    except httpx.HTTPStatusError as e:
        return {
            "success": False,
            "warmup_method": "ollama_native" if is_ollama else "openai_compatible_ping",
            "error": f"HTTP {e.response.status_code}: {str(e)[:200]}",
        }
    except Exception as e:
        return {
            "success": False,
            "warmup_method": "ollama_native" if is_ollama else "openai_compatible_ping",
            "error": f"Warmup error: {type(e).__name__}: {str(e)[:200]}",
        }


def get_model_for_role(config: DebateExecutionConfig, role: str, side: Optional[str] = None) -> str:
    """Select the appropriate model for a given debate role.

    Args:
        config: Debate execution configuration
        role: The debate role (e.g., 'Product Owner', 'Skeptic', 'Final Arbiter')
        side: Optional side ('pro' or 'con') — used to determine model selection

    Returns:
        Model name to use for this role

    Model selection logic:
    - single_model mode: always return default_model
    - role_models mode:
        - Pro/Builder roles (Product Owner, UX/Design Reviewer, Technical Architect, Builder): use pro_model or default_model
        - Con/Skeptic roles (Skeptic/Red Team, Security/Privacy Reviewer): use con_model or default_model
        - Arbiter (Final Arbiter): use arbiter_model or default_model
        - Any other role: use default_model
    """
    # Single-model mode: all roles use default_model
    if config.model_mode == "single_model":
        return config.default_model

    # Role-models mode: select based on role
    role_lower = role.lower()

    # Pro/Builder roles
    if any(p in role_lower for p in ["product owner", "ux", "design reviewer", "technical architect", "builder"]):
        return config.pro_model or config.default_model

    # Con/Skeptic roles
    if any(p in role_lower for p in ["skeptic", "red team", "security", "privacy reviewer"]):
        return config.con_model or config.default_model

    # Arbiter role
    if "arbiter" in role_lower:
        return config.arbiter_model or config.default_model

    # Default fallback
    return config.default_model


def build_debate_prompt(
    work_item: WorkItem,
    operator_inputs: list[OperatorDebateInput],
    previous_arguments: list[dict],
    round_number: int,
    total_rounds: int,
    role: str,
    side: str,
    turn_index: int,
    total_turns_in_round: int,
) -> str:
    """Build a dialectic prompt for a single debate turn.
    
    Each turn must respond to prior opposing claims, not just repeat.
    """
    parts = []

    # Header
    parts.append(f"=== DEBATE ROUND {round_number}/{total_rounds}, TURN {turn_index}/{total_turns_in_round} ===")
    parts.append(f"Role: {role}")
    parts.append(f"Side: {side.upper()}")
    parts.append("")

    # Work item context
    parts.append("=== WORK ITEM ===")
    parts.append(f"Type: {work_item.type.upper()}")
    parts.append(f"Title: {work_item.title}")
    if work_item.body:
        parts.append(f"Description:\\n{work_item.body}")
    if work_item.acceptance_notes:
        parts.append(f"Acceptance Notes:\\n{work_item.acceptance_notes}")
    parts.append("")

    # Operator arguments
    if operator_inputs:
        parts.append("=== OPERATOR ARGUMENTS ===")
        for inp in operator_inputs:
            stance = inp.stance_assigned or inp.stance_requested or "neutral"
            parts.append(f"[{stance.upper()}] {inp.content}")
        parts.append("")

    # Prior arguments from this debate - CRITICAL for dialectic
    if previous_arguments:
        parts.append("=== PRIOR ARGUMENTS IN THIS DEBATE ===")
        parts.append("You MUST read these carefully and respond to the strongest opposing claims.")
        parts.append("Do NOT repeat arguments already made. Build on, rebut, or concede specific points.")
        parts.append("")
        for arg in previous_arguments:
            arg_side = arg.get('side', 'unknown').upper()
            arg_role = arg.get('role', 'Unknown')
            arg_content = arg.get('content', '')[:800]  # Truncate for token bounds
            claim_id = arg.get('claim_id', '')
            parts.append(f"[{arg_side}] {arg_role} (Claim {claim_id}):")
            parts.append(f"  {arg_content}")
            parts.append("")

    # Dialectic instructions
    parts.append("=== YOUR TASK ===")
    if role == "Final Arbiter":
        parts.append("Synthesize all arguments and produce a final recommendation.")
        parts.append("Reference specific claim IDs from both sides in your rationale.")
        parts.append("Your response MUST be valid JSON with this exact structure:")
        parts.append("{")
        parts.append('  "recommendation": "APPROVE_AS_IS|APPROVE_WITH_EDITS|SPLIT_FIRST|NEEDS_MORE_DETAIL|DO_NOT_BUILD_NOW",')
        parts.append('  "implementation_readiness": "READY|READY_AFTER_EDITS|NOT_READY",')
        parts.append('  "rationale": "Reference specific claim IDs and explain which arguments were most persuasive...",')
        parts.append('  "top_pro_claims": ["claim_id_1", "claim_id_2"],')
        parts.append('  "top_con_claims": ["claim_id_3", "claim_id_4"],')
        parts.append('  "top_risks": "...",')
        parts.append('  "suggested_title": "...",')
        parts.append('  "suggested_description": "...",')
        parts.append('  "suggested_acceptance_notes": "..."')
        parts.append("}")
    else:
        # Determine which opposing side to respond to
        opposing_side = "con" if side == "pro" else "pro"
        prior_opposing = [a for a in previous_arguments if a.get('side') == opposing_side]
        
        parts.append(f"As {role}, argue from the {side} perspective.")
        if prior_opposing:
            parts.append(f"CRITICAL: There are {len(prior_opposing)} {opposing_side.upper()} claims made before you.")
            parts.append("You MUST:")
            parts.append("  1. Identify the 1-2 strongest opposing claims by their claim_id")
            parts.append("  2. Either rebut them with specific counter-evidence, concede a valid point, or reframe the issue")
            parts.append("  3. State clearly: 'Responding to claim [X]: ...' or 'I concede that [X] raises a valid point about ...'")
            parts.append("  4. Avoid repeating arguments already made by your side")
            parts.append("  5. If your position evolved after considering the other side, state: 'After considering [X], I now believe...'")
        else:
            parts.append("This is the opening argument for your side. Make a strong, concise case.")
        parts.append("Be specific. Reference work item details. Keep under 800 characters.")
        parts.append("")
        parts.append("Your response MUST be valid JSON:")
        parts.append("{")
        parts.append('  "content": "Your argument text...",')
        parts.append(f'  "responds_to_claim_ids": ["claim_id_you_are_responding_to"],  // empty array [] if opening argument')
        parts.append('  "concession": "What you concede from opponent, or null",')
        parts.append('  "rebuttal": "What you rebut and why, or null",')
        parts.append('  "revised_position": "How your view changed, or null"')
        parts.append("}")

    return "\n".join(parts)


def call_model(
    config: ExecutionConfig,
    messages: list[dict[str, str]],
) -> str:
    """Call the configured model endpoint.

    Uses Ollama native API (/api/chat) for ollama_native providers.
    Uses OpenAI-compatible API (/v1/chat/completions) for openai_compatible providers.

    Returns raw model output (not parsed).
    Raises httpx.RequestError on network/model failures.
    """
    # Route based on provider
    if config.provider == "ollama_native":
        # Ollama native: /api/chat
        url = config.base_url.rstrip("/") + "/api/chat"
        payload = {
            "model": config.model,
            "messages": messages,
            "stream": False,
        }
    else:
        # OpenAI-compatible: /chat/completions
        url = config.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": config.model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2048,
            "stream": False,
        }

    headers = {
        "Content-Type": "application/json",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    with httpx.Client(timeout=config.timeout_seconds) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        # Parse response based on provider
        if config.provider == "ollama_native":
            return data.get("message", {}).get("content", "")
        else:
            return data.get("choices", [{}])[0].get("message", {}).get("content", "")

    # Extract content from OpenAI-compatible response
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(f"Unexpected model response format: {data}") from e

    # Truncate if needed
    if len(content) > config.max_output_chars:
        content = content[: config.max_output_chars] + "\n... [truncated]"

    return content


def parse_arbiter_json(content: str) -> Optional[dict[str, Any]]:
    """Parse Final Arbiter JSON output.

    Returns parsed dict or None if parsing fails.
    """
    # Try to extract JSON from content (may have markdown code fences)
    json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
    if not json_match:
        # Try to find JSON with nested braces
        start = content.find("{")
        if start == -1:
            return None
        # Find matching close brace (simplified)
        depth = 0
        for i, char in enumerate(content[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    json_match = type("Match", (), {"group": lambda _: content[start : i + 1]})()
                    break

    if not json_match:
        return None

    try:
        # Extract JSON string safely
        if hasattr(json_match, "group"):
            json_str = json_match.group(0)
        else:
            json_str = str(json_match)
        parsed = json.loads(json_str)
        # Validate required fields
        if "recommendation" not in parsed:
            return None
        return parsed
    except (json.JSONDecodeError, AttributeError):
        return None


def execute_debate_run(
    db_session,
    run: DebateRun,
    work_item: WorkItem,
    config: ExecutionConfig,
) -> None:
    """Execute a single debate run with model warmup and true dialectic back-and-forth.
    
    Warmup phase (if enabled):
    - Loads the model before starting debate timer
    - Uses warmup_timeout_seconds (separate from generation timeout)
    - Records warmup duration and method
    
    Generation phase:
    - Only starts after warmup completes
    - Uses config.timeout_seconds for actual debate generation
    - Tracks generation timestamps separately
    
    Updates the run in-place with status, arguments, and outcomes.
    Caller must commit the session.
    """
    import uuid
    
    # Load DebateExecutionConfig for warmup settings
    config_row = db_session.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()
    
    # Fetch operator inputs for this work item
    operator_inputs = (
        db_session.query(OperatorDebateInput)
        .filter(OperatorDebateInput.work_item_id == work_item.id)
        .all()
    )
    
    # Mark as queued initially
    run.status = "queued"
    run.execution_stage = "queued"
    run.rounds_completed = 0
    run.model_route = f"{config.provider}:{config.model}"
    run.provenance = f"executed via {config.redacted_base_url()}"
    db_session.flush()
    
    # ===== WARMUP PHASE =====
    warmup_result = None
    if config_row and config_row.warm_model_before_debate:
        run.execution_stage = "warming"
        run.warmup_started_at = datetime.now(timezone.utc)
        db_session.flush()
        
        # Use the resolved config (which has correct provider routing) instead of config_row
        print(f"[WARMUP-PHASE] Using config.provider={config.provider}, config.base_url={config.base_url}", flush=True)
        
        # Perform warmup using the correct provider routing
        if config.provider == "ollama_native":
            success, error, latency_ms = warm_model_ollama_native(
                base_url=config.base_url,
                model=config.model,
                keep_alive=config_row.keep_model_loaded_for,
                timeout_seconds=config_row.warmup_timeout_seconds,
            )
            warmup_result = {"success": success, "error": error, "warmup_method": "ollama_native", "duration_ms": latency_ms}
        else:
            success, error, latency_ms = warm_model_openai_compatible(
                base_url=config.base_url,
                model=config.model,
                api_key=config.api_key,
                timeout_seconds=config_row.warmup_timeout_seconds,
            )
            warmup_result = {"success": success, "error": error, "warmup_method": "openai_compatible", "duration_ms": latency_ms}
        
        if warmup_result["success"]:
            run.warmup_completed_at = datetime.now(timezone.utc)
            run.warmup_duration_ms = warmup_result.get("duration_ms", 0)
            run.warmup_method = warmup_result.get("warmup_method", "unknown")
            run.execution_stage = "running"
            db_session.flush()
        else:
            # Warmup failed
            run.warmup_error = warmup_result.get("error", "Unknown warmup error")
            run.warmup_method = warmup_result.get("warmup_method", "unknown")
            run.warmup_completed_at = datetime.now(timezone.utc)
            
            if config_row and config_row.fail_debate_if_warmup_fails:
                run.status = "failed"
                run.execution_stage = "failed"
                run.error_type = "model_warmup_failed"
                run.error_message = f"Model warmup failed: {run.warmup_error}"
                run.completed_at = datetime.utcnow()
                return
            else:
                # Continue to generation but record warmup failure
                run.execution_stage = "running"
                db_session.flush()
    
    # ===== GENERATION PHASE =====
    run.generation_started_at = datetime.utcnow()
    run.execution_stage = "generating"
    db_session.flush()
    
    print(f"[EXECUTE] Starting generation phase for run {run.id}")
    print(f"[EXECUTE] Config: provider={config.provider}, base_url={config.base_url}, model={config.model}")
    
    # Track all arguments for dialectic context
    all_arguments = []  # List of dicts with claim_id, side, role, content
    generation_error = None
    
    try:
        # Define turn order: sequential pro/con exchanges per round
        # Round 1: PRO opening -> CON response -> PRO reply
        # Round 2+: PRO responds to CON -> CON responds to PRO -> PRO final reply
        turns_per_round = 3  # pro_opening, con_response, pro_reply

        print(f"[EXECUTE] Starting {run.rounds_requested} rounds with {turns_per_round} turns each", flush=True)
        print(f"[EXECUTE] Config timeout: {config.timeout_seconds}s per turn", flush=True)
        
        for round_num in range(1, run.rounds_requested + 1):
            print(f"[EXECUTE] === Starting Round {round_num}/{run.rounds_requested} ===", flush=True)
            # Get arguments from prior rounds for context
            prior_args = all_arguments.copy()
            
            # Update progress before PRO turn
            run.current_round = round_num
            run.current_turn = "pro_opening"
            run.current_side = "pro"
            run.current_role = "Product Owner + Builder"
            run.current_model = config.model
            run.last_progress_at = datetime.utcnow()
            run.progress_message = f"Round {round_num}: PRO opening argument"
            db_session.flush()
            
            print(f"[TURN] Round {round_num} Turn 1: PRO side (timeout={config.timeout_seconds}s)", flush=True)

            # Turn 1: PRO side opening (or response in round 2+)
            pro_roles = ["Product Owner", "Builder"]
            pro_content = _execute_debate_turn(
                db_session=db_session,
                run=run,
                work_item=work_item,
                operator_inputs=operator_inputs,
                config=config,
                round_number=round_num,
                turn_index=1,
                total_turns=turns_per_round,
                side="pro",
                roles=pro_roles,
                prior_arguments=prior_args,
            )
            print(f"[TURN] PRO content result: {pro_content is not None}", flush=True)
            if pro_content:
                all_arguments.append(pro_content)
                print(f"[TURN] Added PRO argument, total args: {len(all_arguments)}", flush=True)

            # Turn 2: CON side response
            print(f"[TURN] Round {round_num} Turn 2: CON side (timeout={config.timeout_seconds}s)", flush=True)
            con_roles = ["UX/Design Reviewer", "Technical Architect", "Security/Privacy Reviewer", "Skeptic/Red Team"]
            # CON sees PRO's argument from this round
            prior_args_with_pro = all_arguments.copy()
            try:
                con_content = _execute_debate_turn(
                    db_session=db_session,
                    run=run,
                    work_item=work_item,
                    operator_inputs=operator_inputs,
                    config=config,
                    round_number=round_num,
                    turn_index=2,
                    total_turns=turns_per_round,
                    side="con",
                    roles=con_roles,
                    prior_arguments=prior_args_with_pro,
                )
                print(f"[TURN] CON content result: {con_content is not None}", flush=True)
                if con_content:
                    all_arguments.append(con_content)
                    print(f"[TURN] Added CON argument, total args: {len(all_arguments)}", flush=True)
            except Exception as e:
                print(f"[TURN] CON turn failed: {type(e).__name__}: {e}", flush=True)
                generation_error = f"CON turn failed: {type(e).__name__}"
                run.error_type = "model_generation_error"
                run.error_stage = f"round_{round_num}_turn_2_con"
                run.error_message = str(e)[:500]
                break  # Exit the round loop

            # Turn 3: PRO side reply to CON
            print(f"[TURN] Round {round_num} Turn 3: PRO reply (timeout={config.timeout_seconds}s)", flush=True)
            prior_args_with_con = all_arguments.copy()
            try:
                pro_reply_content = _execute_debate_turn(
                    db_session=db_session,
                    run=run,
                    work_item=work_item,
                    operator_inputs=operator_inputs,
                    config=config,
                    round_number=round_num,
                    turn_index=3,
                    total_turns=turns_per_round,
                    side="pro",
                    roles=pro_roles,
                    prior_arguments=prior_args_with_con,
                    is_reply=True,
                )
                print(f"[TURN] PRO reply result: {pro_reply_content is not None}", flush=True)
                if pro_reply_content:
                    all_arguments.append(pro_reply_content)
                    print(f"[TURN] Added PRO reply argument, total args: {len(all_arguments)}", flush=True)
            except Exception as e:
                print(f"[TURN] PRO reply turn failed: {type(e).__name__}: {e}", flush=True)
                generation_error = f"PRO reply turn failed: {type(e).__name__}"
                run.error_type = "model_generation_error"
                run.error_stage = f"round_{round_num}_turn_3_pro_reply"
                run.error_message = str(e)[:500]
                break  # Exit the round loop

            # End of round
            db_session.flush()
            run.rounds_completed = round_num
            print(f"[EXECUTE] Round {round_num} complete, rounds_completed={run.rounds_completed}", flush=True)

        # ===== FINAL ARBITER PHASE =====
        print(f"[EXECUTE] All rounds complete, calling Final Arbiter...", flush=True)
        print(f"[EXECUTE] Total arguments for arbiter: {len(all_arguments)}", flush=True)
        
        # Final Arbiter round - sees all arguments
        arbiter_data = _execute_arbiter_turn(
            db_session=db_session,
            run=run,
            work_item=work_item,
            config=config,
            all_arguments=all_arguments,
        )
        print(f"[ARBITER] Result: {arbiter_data is not None}", flush=True)

        if arbiter_data:
            print(f"[ARBITER] Recommendation: {arbiter_data.get('recommendation')}", flush=True)
            run.final_recommendation = arbiter_data.get("recommendation")
            run.implementation_readiness = arbiter_data.get("implementation_readiness")
            run.summary = arbiter_data.get("rationale")
            run.risks = arbiter_data.get("top_risks")
            run.suggested_title = arbiter_data.get("suggested_title")
            run.suggested_description = arbiter_data.get("suggested_description")
            run.suggested_acceptance_notes = arbiter_data.get("suggested_acceptance_notes")
        else:
            print(f"[ARBITER] No data returned!", flush=True)

        # Mark operator inputs as considered
        for inp in operator_inputs:
            if not inp.considered_in_run_id:
                inp.considered_in_run_id = run.id
                if inp.stance_requested == "auto_assign":
                    inp.stance_assigned = "neutral"
                db_session.add(inp)

        # Success
        run.status = "completed"
        run.execution_stage = "completed"
        run.generation_completed_at = datetime.utcnow()
        run.generation_duration_ms = int((run.generation_completed_at - run.generation_started_at).total_seconds() * 1000) if run.generation_started_at else None
        run.completed_at = datetime.utcnow()

    except httpx.TimeoutException as e:
        generation_error = f"Model generation timeout: {type(e).__name__}"
        run.error_type = "model_generation_timeout"
        run.error_stage = "generation"
        run.error_round = run.current_round
        run.error_turn = run.current_turn
        run.error_elapsed_ms = int((datetime.utcnow() - run.generation_started_at).total_seconds() * 1000) if run.generation_started_at else None
    except httpx.RequestError as e:
        generation_error = f"Model endpoint error: {type(e).__name__}"
        run.error_type = "model_read_timeout" if "timeout" in str(e).lower() else "model_provider_unreachable"
        run.error_stage = "generation"
        run.error_round = run.current_round
        run.error_turn = run.current_turn
    except ValueError as e:
        generation_error = f"Model response parsing error: {str(e)[:200]}"
        run.error_type = "model_response_parse_error"
        run.error_stage = "generation"
    except Exception as e:
        generation_error = f"Unexpected error: {type(e).__name__}"
        run.error_type = "unexpected_error"
        run.error_stage = "generation"
    
    # Handle generation failure
    if generation_error:
        run.status = "failed"
        run.execution_stage = "failed"
        run.error_message = generation_error
        run.generation_completed_at = datetime.utcnow()
        run.generation_duration_ms = int((run.generation_completed_at - run.generation_started_at).total_seconds() * 1000) if run.generation_started_at else None
        run.completed_at = datetime.utcnow()


def _generate_claim_id(round_num: int, side: str, role_short: str, turn_index: int) -> str:
    """Generate a unique claim ID like R1-PRO-PO-001."""
    side_code = "PRO" if side == "pro" else "CON" if side == "con" else "ARB"
    role_map = {
        "Product Owner": "PO",
        "UX/Design Reviewer": "UX",
        "Technical Architect": "ARCH",
        "Security/Privacy Reviewer": "SEC",
        "Builder": "BLD",
        "Skeptic/Red Team": "SKP",
        "Final Arbiter": "ARB",
    }
    role_code = role_map.get(role_short, "UNK")
    return f"R{round_num}-{side_code}-{role_code}-{turn_index:03d}"


def _execute_debate_turn(
    db_session,
    run: DebateRun,
    work_item: WorkItem,
    operator_inputs: list,
    config: ExecutionConfig,
    round_number: int,
    turn_index: int,
    total_turns: int,
    side: str,
    roles: list,
    prior_arguments: list,
    is_reply: bool = False,
) -> Optional[dict]:
    """Execute a single debate turn for one side.
    
    Returns dict with claim_id, side, role, content for tracking.
    """
    # Combine roles into one argument for this turn
    role_str = " + ".join(roles) if len(roles) > 1 else roles[0]
    role_short = roles[0] if len(roles) == 1 else "Multiple"
    
    # Generate claim ID
    claim_id = _generate_claim_id(round_number, side, role_short, turn_index)
    
    # Find opposing claims to respond to
    opposing_side = "con" if side == "pro" else "pro"
    opposing_claims = [a for a in prior_arguments if a.get('side') == opposing_side]
    responds_to = [a['claim_id'] for a in opposing_claims[-2:]] if opposing_claims else []
    
    # Build prompt with prior arguments
    prompt = build_debate_prompt(
        work_item=work_item,
        operator_inputs=[i for i in operator_inputs if not i.considered_in_run_id],
        previous_arguments=prior_arguments,
        round_number=round_number,
        total_rounds=run.rounds_requested,
        role=role_str,
        side=side,
        turn_index=turn_index,
        total_turns_in_round=total_turns,
    )

    # Call model
    messages = [
        {"role": "system", "content": "You are participating in a structured debate. Respond in valid JSON only."},
        {"role": "user", "content": prompt},
    ]

    content = call_model(config, messages)
    
    # Parse JSON response
    try:
        # Extract JSON from content
        json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
        if json_match:
            json_str = json_match.group(0)
            parsed = json.loads(json_str)
        else:
            # Fallback: treat entire content as the argument
            parsed = {"content": content, "responds_to_claim_ids": [], "concession": None, "rebuttal": None, "revised_position": None}
        
        argument_content = parsed.get("content", content)[:config.max_output_chars]
        responds_to_claim_ids = parsed.get("responds_to_claim_ids", responds_to)
        concession = parsed.get("concession")
        rebuttal = parsed.get("rebuttal")
        revised_position = parsed.get("revised_position")
    except (json.JSONDecodeError, AttributeError):
        argument_content = content[:config.max_output_chars]
        responds_to_claim_ids = responds_to
        concession = None
        rebuttal = None
        revised_position = None

    # Store argument
    arg = DebateArgument(
        debate_run_id=run.id,
        round_number=round_number,
        role=role_str,
        side=side,
        content=argument_content,
        claim_id=claim_id,
        responds_to_claim_ids=json.dumps(responds_to_claim_ids) if responds_to_claim_ids else None,
        concession=concession,
        rebuttal=rebuttal,
        revised_position=revised_position,
    )
    db_session.add(arg)
    db_session.flush()

    return {
        "claim_id": claim_id,
        "side": side,
        "role": role_str,
        "content": argument_content,
        "round_number": round_number,
    }


def _execute_arbiter_turn(
    db_session,
    run: DebateRun,
    work_item: WorkItem,
    config: ExecutionConfig,
    all_arguments: list,
) -> Optional[dict]:
    """Execute Final Arbiter turn."""
    print(f"[ARBITER] Starting with {len(all_arguments)} arguments...", flush=True)
    
    # Truncate arguments for arbiter to avoid timeout - use last 4 only for faster response
    truncated_args = all_arguments[-4:] if len(all_arguments) > 4 else all_arguments
    print(f"[ARBITER] Using {len(truncated_args)} arguments (truncated from {len(all_arguments)})", flush=True)
    
    arbiter_prompt = build_debate_prompt(
        work_item=work_item,
        operator_inputs=[],
        previous_arguments=truncated_args,
        round_number=run.rounds_requested + 1,
        total_rounds=run.rounds_requested + 1,
        role="Final Arbiter",
        side="arbiter",
        turn_index=1,
        total_turns_in_round=1,
    )
    
    print(f"[ARBITER] Prompt length: {len(arbiter_prompt)} chars", flush=True)

    messages = [
        {"role": "system", "content": "You are the Final Arbiter. Produce valid JSON only. Be concise."},
        {"role": "user", "content": arbiter_prompt},
    ]

    # Arbiter needs more time - use 180s timeout
    print(f"[ARBITER] Calling model (timeout=180s for arbiter)...", flush=True)
    import httpx
    with httpx.Client(timeout=180) as client:
        url = config.base_url.rstrip("/") + "/api/chat" if config.provider == "ollama_native" else config.base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        payload = {"model": config.model, "messages": messages, "stream": False}
        if config.provider != "ollama_native":
            payload["temperature"] = 0.7
            payload["max_tokens"] = 2048
        
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
        
        if config.provider == "ollama_native":
            arbiter_content = data.get("message", {}).get("content", "")
        else:
            arbiter_content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    
    print(f"[ARBITER] Response length: {len(arbiter_content) if arbiter_content else 0} chars", flush=True)
    
    arbiter_data = parse_arbiter_json(arbiter_content)
    print(f"[ARBITER] Parsed result: {arbiter_data is not None}", flush=True)

    if arbiter_data:
        # Store arbiter argument
        claim_id = _generate_claim_id(run.rounds_requested + 1, "arbiter", "Final Arbiter", 1)
        all_claim_ids = [a["claim_id"] for a in all_arguments]
        
        arbiter_arg = DebateArgument(
            debate_run_id=run.id,
            round_number=run.rounds_requested + 1,
            role="Final Arbiter",
            side="arbiter",
            content=arbiter_content,
            claim_id=claim_id,
            responds_to_claim_ids=json.dumps(all_claim_ids[-6:]),  # Reference last 6 claims
        )
        db_session.add(arbiter_arg)
        db_session.flush()

    return arbiter_data
