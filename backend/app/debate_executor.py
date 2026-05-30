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
from datetime import datetime
from typing import Any, Optional

import httpx
from sqlalchemy.orm import Session

from .models import DebateArgument, DebateExecutionConfig, DebateRun, OperatorDebateInput, WorkItem


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
    """
    # Try DB first
    config_row = db.query(DebateExecutionConfig).filter(DebateExecutionConfig.id == 1).first()

    if config_row is not None:
        return ExecutionConfig(
            enabled=config_row.enabled,
            provider=config_row.provider,
            base_url=config_row.base_url,
            model=config_row.model,
            api_key=config_row.api_key,
            timeout_seconds=config_row.timeout_seconds,
            max_output_chars=config_row.max_output_chars,
        )

    # Fallback to environment (bootstrap only)
    return ExecutionConfig.from_env()


def build_debate_prompt(
    work_item: WorkItem,
    operator_inputs: list[OperatorDebateInput],
    previous_summary: Optional[str],
    changes_since_previous: Optional[dict],
    round_number: int,
    total_rounds: int,
    role: str,
    side: str,
) -> str:
    """Build a compact prompt for a single debate role.

    Keeps context bounded to avoid token overrun.
    """
    parts = []

    # Header
    parts.append(f"=== DEBATE ROUND {round_number}/{total_rounds} ===")
    parts.append(f"Role: {role}")
    parts.append(f"Side: {side.upper()}")
    parts.append("")

    # Work item context
    parts.append("=== WORK ITEM ===")
    parts.append(f"Type: {work_item.type.upper()}")
    parts.append(f"Title: {work_item.title}")
    parts.append(f"Status: {work_item.status}")
    parts.append(f"Priority: {work_item.priority}")
    if work_item.target_app:
        parts.append(f"Target App: {work_item.target_app}")
    if work_item.body:
        parts.append(f"Description:\n{work_item.body}")
    if work_item.acceptance_notes:
        parts.append(f"Acceptance Notes:\n{work_item.acceptance_notes}")
    if work_item.tags:
        parts.append(f"Tags: {work_item.tags}")
    parts.append("")

    # Operator arguments
    if operator_inputs:
        parts.append("=== OPERATOR ARGUMENTS ===")
        for inp in operator_inputs:
            stance = inp.stance_assigned or inp.stance_requested or "neutral"
            parts.append(f"[{stance.upper()}] {inp.content}")
        parts.append("")

    # Previous debate context
    if previous_summary:
        parts.append("=== PREVIOUS DEBATE SUMMARY ===")
        parts.append(previous_summary)
        parts.append("")

    if changes_since_previous:
        parts.append("=== CHANGES SINCE LAST DEBATE ===")
        for field, change in changes_since_previous.items():
            parts.append(f"{field}: {change.get('from')} → {change.get('to')}")
        parts.append("")

    # Role-specific instructions
    parts.append("=== YOUR TASK ===")
    if role == "Final Arbiter":
        parts.append("Synthesize all arguments and produce a final recommendation.")
        parts.append("Your response MUST be valid JSON with this exact structure:")
        parts.append("{")
        parts.append('  "recommendation": "APPROVE_AS_IS|APPROVE_WITH_EDITS|SPLIT_FIRST|NEEDS_MORE_DETAIL|DO_NOT_BUILD_NOW",')
        parts.append('  "implementation_readiness": "READY|READY_AFTER_EDITS|NOT_READY",')
        parts.append('  "rationale": "...",')
        parts.append('  "top_risks": "...",')
        parts.append('  "suggested_title": "...",')
        parts.append('  "suggested_description": "...",')
        parts.append('  "suggested_acceptance_notes": "..."')
        parts.append("}")
    else:
        parts.append(f"As {role}, argue from the {side} perspective.")
        parts.append("Be concise and specific. Reference the work item details.")
        parts.append("If side is 'neutral', provide context and analysis without taking a position.")

    return "\n".join(parts)


def call_model(
    config: ExecutionConfig,
    messages: list[dict[str, str]],
) -> str:
    """Call the configured model endpoint.

    Returns raw model output (not parsed).
    Raises httpx.RequestError on network/model failures.
    """
    url = config.base_url.rstrip("/") + "/chat/completions"

    headers = {
        "Content-Type": "application/json",
    }
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"

    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 2048,
        "stream": False,
    }

    with httpx.Client(timeout=config.timeout_seconds) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()

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
    """Execute a single debate run.

    Updates the run in-place with status, arguments, and outcomes.
    Caller must commit the session.
    """
    from sqlalchemy.orm import make_transient

    # Fetch operator inputs for this work item
    operator_inputs = (
        db_session.query(OperatorDebateInput)
        .filter(OperatorDebateInput.work_item_id == work_item.id)
        .all()
    )

    # Get previous debate summary if this is a rerun
    previous_summary = None
    changes_since_previous = None
    if run.changed_since_previous_json:
        try:
            changes_since_previous = json.loads(run.changed_since_previous_json)
        except (json.JSONDecodeError, TypeError):
            pass

    # Mark as running
    run.status = "running"
    run.rounds_completed = 0
    run.model_route = f"{config.provider}:{config.model}"
    run.provenance = f"executed via {config.redacted_base_url()}"

    try:
        # Execute each round
        for round_num in range(1, run.rounds_requested + 1):
            # Execute each role for this round
            for role in DEBATE_ROLES:
                # Determine side for this role
                # (simplified: all roles argue both pro and con across rounds)
                if round_num % 2 == 1:
                    side = "pro" if role in ["Product Owner", "Builder"] else "con"
                else:
                    side = "con" if role in ["Product Owner", "Builder"] else "pro"

                # Build prompt
                prompt = build_debate_prompt(
                    work_item=work_item,
                    operator_inputs=[i for i in operator_inputs if not i.considered_in_run_id],
                    previous_summary=previous_summary,
                    changes_since_previous=changes_since_previous if round_num == 1 else None,
                    round_number=round_num,
                    total_rounds=run.rounds_requested,
                    role=role,
                    side=side,
                )

                # Call model
                messages = [
                    {"role": "system", "content": "You are participating in a structured debate about a software work item. Be concise, specific, and constructive."},
                    {"role": "user", "content": prompt},
                ]

                content = call_model(config, messages)

                # Store argument
                arg = DebateArgument(
                    debate_run_id=run.id,
                    round_number=round_num,
                    role=role,
                    side=side,
                    content=content,
                )
                db_session.add(arg)

            # End of round — flush to DB
            db_session.flush()
            run.rounds_completed = round_num

        # Final Arbiter round
        arbiter_prompt = build_debate_prompt(
            work_item=work_item,
            operator_inputs=[],
            previous_summary=None,
            changes_since_previous=None,
            round_number=run.rounds_requested + 1,
            total_rounds=run.rounds_requested + 1,
            role="Final Arbiter",
            side="arbiter",
        )

        messages = [
            {"role": "system", "content": "You are the Final Arbiter in a software work item debate. Synthesize all arguments and produce a structured recommendation. Your response MUST be valid JSON."},
            {"role": "user", "content": arbiter_prompt},
        ]

        arbiter_content = call_model(config, messages)

        # Parse arbiter JSON
        arbiter_data = parse_arbiter_json(arbiter_content)

        if arbiter_data:
            run.final_recommendation = arbiter_data.get("recommendation")
            run.implementation_readiness = arbiter_data.get("implementation_readiness")
            run.summary = arbiter_data.get("rationale")
            run.risks = arbiter_data.get("top_risks")
            run.suggested_title = arbiter_data.get("suggested_title")
            run.suggested_description = arbiter_data.get("suggested_description")
            run.suggested_acceptance_notes = arbiter_data.get("suggested_acceptance_notes")

            # Store arbiter argument
            arbiter_arg = DebateArgument(
                debate_run_id=run.id,
                round_number=run.rounds_requested + 1,
                role="Final Arbiter",
                side="arbiter",
                content=arbiter_content,
            )
            db_session.add(arbiter_arg)

        # Mark operator inputs as considered
        for inp in operator_inputs:
            if not inp.considered_in_run_id:
                inp.considered_in_run_id = run.id
                if inp.stance_requested == "auto_assign":
                    # Auto-assign based on content sentiment (simplified)
                    inp.stance_assigned = "neutral"
                db_session.add(inp)

        # Success
        run.status = "completed"
        run.completed_at = datetime.utcnow()

    except httpx.RequestError as e:
        # Network/model error
        run.status = "failed"
        run.error_message = f"Model endpoint error: {type(e).__name__}"
        run.completed_at = datetime.utcnow()
    except ValueError as e:
        # Parse error
        run.status = "failed"
        run.error_message = f"Model response parsing error: {str(e)[:200]}"
        run.completed_at = datetime.utcnow()
    except Exception as e:
        # Unexpected error
        run.status = "failed"
        run.error_message = f"Unexpected error: {type(e).__name__}"
        run.completed_at = datetime.utcnow()
