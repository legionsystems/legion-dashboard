"""Implementation Kanban card assembly for the Hermes Kanban builder.

This module is the single source of truth for turning a Work Item + the
latest completed DebateRun into the prompt body that the Hermes Kanban
builder receives. It deliberately lives in its own file so that the
assembly rules (canonical mandatory edits, readiness passthrough,
out-of-scope contradiction check) are testable in isolation and so
that any caller — including a future "start build" UI affordance —
renders the same builder prompt.

The rules implemented here are scoped to PROMPT-LEVEL quality only.
They do not change debate execution, arbiter model/provider routing,
or any WI-16/WI-17 business logic.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

from .models import DebateArgument, DebateRun, WorkItem


# ---------------------------------------------------------------------------
# Mandatory edits extraction
# ---------------------------------------------------------------------------


# Known fields we expect on a mandatory edit entry. Anything else is passed
# through as an extra bullet for completeness.
MANDATORY_EDIT_KNOWN_FIELDS: Sequence[str] = (
    "field",
    "current_problem",
    "required_change",
)


def _normalize_mandatory_edit(raw: dict) -> dict:
    """Return a mandatory edit dict with the known fields plus extras.

    Missing fields are rendered as ``(not provided)`` by the renderer, so
    we keep the structure faithful even when the arbiter omitted a field.
    """
    if not isinstance(raw, dict):
        # Bad data — surface it as a synthetic entry so the builder still
        # sees the issue and the operator can correct the source.
        return {
            "field": "(unparseable edit)",
            "current_problem": "(not provided)",
            "required_change": json.dumps(raw)[:500] or "(not provided)",
        }
    out: dict = {}
    for key in MANDATORY_EDIT_KNOWN_FIELDS:
        value = raw.get(key)
        out[key] = value if isinstance(value, str) and value.strip() else None
    for key, value in raw.items():
        if key in MANDATORY_EDIT_KNOWN_FIELDS:
            continue
        if value is None:
            continue
        out[key] = value
    return out


def _extract_first_json_object(text: str) -> Optional[str]:
    """Return the first balanced ``{...}`` substring of ``text``.

    Mirrors the depth-tracking behaviour of
    :func:`app.debate_executor.parse_arbiter_json` so we accept the same
    arbiter outputs the executor accepts (including JSON surrounded by
    stray prose). Returns ``None`` if no balanced object is found.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_arbiter_json(arbiter_content: str) -> Optional[dict]:
    """Parse the Final Arbiter's stored JSON content.

    Handles three storage shapes:

    1. Strict JSON: ``{"recommendation": ...}``
    2. Markdown-fenced JSON: ``\\`\\`\\`json ... \\`\\`\\```
    3. JSON wrapped in stray prose: ``"Here is the JSON: {...}"``

    This matches what the debate executor accepts on the way in (its
    :func:`parse_arbiter_json` strips fences and extracts a balanced
    object). Without (3), ``load_arbiter_mandatory_edits`` would
    silently drop ``mandatory_edits`` for any arbiter that added even
    a one-word prefix/suffix to its JSON.

    Returns ``None`` if no JSON object can be found or parsed.
    """
    if not arbiter_content:
        return None
    text = arbiter_content.strip()
    if text.startswith("```"):
        # Strip ```json / ``` fences if the parser left them in.
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    # Try strict parse first (cheap path).
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        # Fall back to extracting the first balanced JSON object.
        # This is what the executor does when the model returns JSON
        # wrapped in stray prose.
        candidate = _extract_first_json_object(text)
        if candidate is None:
            return None
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            return None
    return parsed if isinstance(parsed, dict) else None


def load_arbiter_mandatory_edits(
    db: Session,
    debate_run: DebateRun,
) -> List[dict]:
    """Load the latest Final Arbiter's ``mandatory_edits`` for a debate run.

    The structured arbiter JSON is stored on the ``DebateArgument`` row
    whose ``side == "arbiter"`` and ``role == "Final Arbiter"``. We do
    not (yet) persist the JSON in a dedicated column, so we read it back
    from the argument content. This keeps the change small and avoids a
    schema migration in this task.

    Returns an empty list if no arbiter argument is found, if it cannot
    be parsed as JSON, or if the parsed JSON has no ``mandatory_edits``.
    """
    if debate_run is None:
        return []
    arbiter_arg = (
        db.query(DebateArgument)
        .filter(
            DebateArgument.debate_run_id == debate_run.id,
            DebateArgument.side == "arbiter",
        )
        .order_by(DebateArgument.id.desc())
        .first()
    )
    if not arbiter_arg or not arbiter_arg.content:
        return []
    parsed = _parse_arbiter_json(arbiter_arg.content)
    if not parsed:
        return []
    edits = parsed.get("mandatory_edits")
    if not isinstance(edits, list):
        return []
    return [_normalize_mandatory_edits_safe(e) for e in edits]


def _normalize_mandatory_edits_safe(raw) -> dict:
    if isinstance(raw, dict):
        return _normalize_mandatory_edit(raw)
    # Non-object entries (string, list, etc.) — surface as a synthetic
    # edit so the builder sees something useful.
    return {
        "field": "(unparseable edit)",
        "current_problem": "(not provided)",
        "required_change": json.dumps(raw)[:500] or "(not provided)",
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_mandatory_edits_section(edits: Sequence[dict]) -> str:
    """Render a canonical ``MANDATORY EDITS FROM DEBATE`` block.

    Each edit is rendered as a structured bullet with the three known
    fields. Missing fields are marked ``(not provided)``. Any extra
    fields on the edit (e.g. ``severity``) are appended for completeness.
    """
    if not edits:
        return ""

    lines: List[str] = ["MANDATORY EDITS FROM DEBATE", ""]
    for idx, edit in enumerate(edits, start=1):
        lines.append(f"- Edit {idx}:")
        for key in MANDATORY_EDIT_KNOWN_FIELDS:
            value = edit.get(key)
            if value:
                lines.append(f"  - {key}: {value}")
            else:
                lines.append(f"  - {key}: (not provided)")
        for key, value in edit.items():
            if key in MANDATORY_EDIT_KNOWN_FIELDS:
                continue
            lines.append(f"  - {key}: {value}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Out-of-scope contradiction check
# ---------------------------------------------------------------------------


# Default generic out-of-scope items the prompt template currently emits.
# The contradiction check operates on these so we can auto-remove a
# conflicting line and emit a ``PROMPT ASSEMBLY WARNING`` rather than
# blocking the entire build.
DEFAULT_OUT_OF_SCOPE_ITEMS: Sequence[str] = (
    "Do not implement Planning Chat, Discord notifications, attachments, debate display modes, clear/reset debates, Re-run Arbiter, operator-argument stance auto-assign, model/provider settings, or unrelated debate repair work.",
    "Do not use direct GitHub API orchestration.",
    "Do not create external/public demo or staging deployments unless already part of the existing Hermes implementation flow.",
)


def _normalize_phrase(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase.strip().lower())


def _extract_phrase_tokens(phrase: str, min_len: int = 4) -> List[str]:
    """Split a phrase into meaningful tokens for overlap detection.

    We drop short tokens, stopwords, and punctuation so the overlap is
    based on real concept words (e.g. "auto-assign", "stance", "AUTO_ASSIGN").

    Underscores, hyphens, and case differences are normalized so that
    ``operator_argument_stance``, ``operator-argument``, and
    ``OPERATOR_ARGUMENT`` all map to the same concept tokens. Without
    this, a work item that names a concept in one form (e.g. the
    mandatory edit field ``operator_argument_stance``) and the
    out-of-scope line that names it in another form (``operator-argument
    stance auto-assign``) would fail to match — silently emitting a
    contradictory prompt.
    """
    if not phrase:
        return []
    # Normalize separators so tokenization is case- and separator-
    # insensitive. Treat underscores, hyphens, and slashes as whitespace.
    normalized = re.sub(r"[_\-/]+", " ", phrase)
    raw = re.findall(r"[A-Za-z][A-Za-z0-9]+", normalized)
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "into", "your",
        "you", "any", "all", "anywhere", "into", "only", "must", "not",
        "do", "does", "are", "was", "were", "but", "yet", "nor", "or",
        "after", "before", "while", "until", "than", "via", "per", "each",
        "every", "its", "his", "her", "their", "our", "who", "whom",
        "whose", "which", "what", "where", "when", "why", "how", "use",
        "work", "make", "made", "make", "made", "have", "has", "had",
        "also", "such", "then", "been", "will", "would", "should",
        "could", "shall", "may", "might", "can", "cannot", "cant",
        "wont", "dont", "doesnt", "didnt", "isnt", "arent", "wasnt",
        "werent", "hasnt", "havent", "hadnt", "shouldnt", "wouldnt",
        "couldnt", "mustnt", "move", "moves", "moved", "moving",
        "add", "adds", "added", "adding", "set", "sets", "setting",
        "top", "bottom", "left", "right", "side", "entry", "entries",
        "page", "pages", "view", "views", "click", "button", "buttons",
    }
    out: List[str] = []
    for tok in raw:
        if len(tok) < min_len:
            continue
        if tok.lower() in stop:
            continue
        out.append(tok.lower())
    return out


def _in_scope_phrase_haystack(
    work_item: WorkItem,
    mandatory_edits: Sequence[dict],
) -> List[str]:
    """Build the list of in-scope key phrases to test against out-of-scope.

    Includes the work item title, body, acceptance notes, and for each
    mandatory edit its ``field`` and ``required_change`` text.
    """
    haystack: List[str] = []
    if work_item.title:
        haystack.append(work_item.title)
    if work_item.body:
        haystack.append(work_item.body)
    if work_item.acceptance_notes:
        haystack.append(work_item.acceptance_notes)
    for edit in mandatory_edits:
        for key in ("field", "required_change"):
            value = edit.get(key)
            if value:
                haystack.append(f"{key}:{value}")
                haystack.append(value)
    # Dedupe while preserving order.
    seen: set = set()
    out: List[str] = []
    for h in haystack:
        norm = _normalize_phrase(h)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(h)
    return out


# Polarity markers. If both the out-of-scope item and the in-scope
# haystack phrase share the same negation polarity, the haystack is
# reinforcing the out-of-scope (NOT a contradiction). If the polarity
# differs (e.g. out-of-scope says "Do not X" and the haystack
# positively asserts "implement X"), that IS a contradiction. If only
# one side has a polarity marker, the match is a regular contradiction
# detection and proceeds as before.
_NEGATION_PREFIXES = (
    "do not", "don't", "must not", "mustn't", "no ", "not ", "never ",
    "without", "avoid", "prohibit", "prohibited",
)


def _polarity_is_negated(phrase: str) -> bool:
    norm = phrase.strip().lower()
    return any(norm.startswith(prefix) or f" {prefix}" in norm for prefix in _NEGATION_PREFIXES)


def _phrase_overlaps(needle_phrase: str, haystack_phrase: str) -> bool:
    """Return True if a substantive concept overlaps between phrases.

    A single common word (e.g. "implement", "feature", "support") is
    NOT enough on its own — that produces false positives on broad
    out-of-scope lines like "Do not implement Planning Chat..." which
    share the verb with almost every implementation work item. We
    require either:

    * two or more distinct concept tokens in the intersection, OR
    * a multi-word substring match (e.g. "auto assign" in both
      phrases, after hyphen normalization).

    Polarity matters: if both phrases share the same negation
    (e.g. out-of-scope says "Do not use direct GitHub API
    orchestration" and the work item acceptance note says "Must not
    use direct GitHub API orchestration") the haystack is REINFORCING
    the out-of-scope, not contradicting it. Polarity-matched matches
    never count as contradictions.
    """
    needle_negated = _polarity_is_negated(needle_phrase)
    haystack_negated = _polarity_is_negated(haystack_phrase)
    # Same-polarity match: the haystack is restating the out-of-scope,
    # not inverting it. Never a contradiction.
    if needle_negated == haystack_negated and (needle_negated or haystack_negated):
        return False

    needle_tokens = set(_extract_phrase_tokens(needle_phrase))
    haystack_tokens = set(_extract_phrase_tokens(haystack_phrase))
    if not needle_tokens or not haystack_tokens:
        return False
    # Multi-token intersection — single common words are not contradictions.
    common = needle_tokens & haystack_tokens
    if len(common) >= 2:
        return True
    # Multi-word substring match: detect "auto assign" vs "auto-assign"
    # style variants, including underscore/case differences. This
    # needs at least 2 tokens on each side so a single generic word
    # does not trigger.
    def _sep_normalize(s: str) -> str:
        return _normalize_phrase(s).replace("-", " ").replace("_", " ")

    norm_needle = _sep_normalize(needle_phrase)
    norm_hay = _sep_normalize(haystack_phrase)
    if norm_needle and norm_needle in norm_hay and len(norm_needle.split()) >= 2:
        return True
    if norm_hay and norm_hay in norm_needle and len(norm_hay.split()) >= 2:
        return True
    return False


@dataclass
class ContradictionCheckResult:
    """Outcome of a contradiction check between in-scope and out-of-scope."""

    filtered_out_of_scope: List[str] = field(default_factory=list)
    removed_items: List[dict] = field(default_factory=list)
    blocked: bool = False
    block_reason: Optional[str] = None


def check_out_of_scope_contradictions(
    work_item: WorkItem,
    mandatory_edits: Sequence[dict],
    out_of_scope_items: Sequence[str],
) -> ContradictionCheckResult:
    """Find and remove (or block on) contradictory out-of-scope items.

    For each out-of-scope item, we look for a substantive phrase match
    against the in-scope haystack (work item title/body/acceptance notes
    plus mandatory edit ``field`` / ``required_change``). When matched,
    the out-of-scope item is removed from the rendered prompt and a
    structured ``removed_item`` record is returned so the caller can
    emit a ``PROMPT ASSEMBLY WARNING`` block.

    The check never silently emits a contradictory card. The
    ``ContradictionCheckResult.removed_items`` list is the source of
    truth for the warning.
    """
    haystack = _in_scope_phrase_haystack(work_item, mandatory_edits)
    result = ContradictionCheckResult()
    for item in out_of_scope_items:
        matched_phrases: List[str] = []
        for phrase in haystack:
            if _phrase_overlaps(item, phrase):
                matched_phrases.append(phrase)
        if matched_phrases:
            result.removed_items.append(
                {
                    "removed_item": item,
                    "matched_phrase": matched_phrases[0],
                    "reason": (
                        "out-of-scope item contradicts the work item title, "
                        "acceptance notes, or a mandatory edit"
                    ),
                }
            )
            continue
        result.filtered_out_of_scope.append(item)
    return result


def render_prompt_assembly_warnings(removed_items: Sequence[dict]) -> str:
    """Render the ``PROMPT ASSEMBLY WARNING`` block, if any."""
    if not removed_items:
        return ""
    lines: List[str] = ["PROMPT ASSEMBLY WARNING", ""]
    for entry in removed_items:
        lines.append(f"- Removed item: {entry.get('removed_item', '')}")
        lines.append(f"  Reason: {entry.get('reason', '')}")
        matched = entry.get("matched_phrase")
        if matched:
            lines.append(f"  Matched in-scope phrase: {matched}")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def build_implementation_card_prompt(
    work_item: WorkItem,
    *,
    debate_run_id: Optional[int],
    recommendation: Optional[str],
    implementation_readiness: Optional[str],
    mandatory_edits: Sequence[dict],
    contradiction_warnings: Sequence[dict] = (),
    target_repo: str = "/srv/repo/legion-dashboard",
    target_worktree: Optional[str] = None,
    feature_branch: Optional[str] = None,
    chosen_base_ref: Optional[str] = None,
) -> str:
    """Render the final implementation Kanban card body for a work item.

    This is the single source of truth for the builder prompt body. It
    does not touch debate execution, the arbiter, the repo safety gate,
    or the Hermes bridge HTTP call — those stay in ``routers/builder.py``.

    ``target_worktree`` is the per-task worktree path under
    ``/srv/worktrees/`` that the builder should use for ALL repo, git,
    test, build, and Codex review operations. When provided, the
    prompt renders an explicit ``TARGET WORKTREE`` block, a
    ``SHARED REPO RULE`` warning, and a ``WORKTREE RULE`` that
    includes a fail-fast check: any attempt to operate in the shared
    ``/srv/repo/legion-dashboard`` operator/control worktree must
    block.

    ``target_repo`` is the legacy alias kept for backwards
    compatibility with the safety gate. It is now derived from the
    worktree path when a worktree is provided.
    """
    # If the caller passed a task worktree, that becomes the
    # authoritative target. ``target_repo`` is kept in sync so the
    # rest of the prompt (PHASE 1, secret scan, Codex review) sees
    # the same path. If the caller passed the shared repo as
    # ``target_repo`` and no worktree, the prompt body still emits
    # the shared-repo warning so an operator sees the inconsistency.
    shared_repo_for_wi = _resolve_shared_repo_path(work_item)
    if target_worktree:
        effective_target_repo = target_worktree
        using_worktree = True
    else:
        effective_target_repo = target_repo
        using_worktree = _is_under_worktrees(target_repo)
    # Render mandatory edits as a canonical section if present.
    mandatory_edits_block = render_mandatory_edits_section(mandatory_edits)

    # Render out-of-scope with the contradiction check already applied.
    check = check_out_of_scope_contradictions(
        work_item, mandatory_edits, DEFAULT_OUT_OF_SCOPE_ITEMS
    )
    if not check.removed_items and contradiction_warnings:
        # Allow the caller to pass in pre-computed warnings (e.g. from
        # an upstream pass). They get rendered verbatim.
        check.removed_items = list(contradiction_warnings)
    out_of_scope_items = list(check.filtered_out_of_scope) or list(
        DEFAULT_OUT_OF_SCOPE_ITEMS
    )
    if check.removed_items:
        # Use the freshly-filtered list (caller's pre-computed warnings
        # only apply when the default list was used as input).
        out_of_scope_items = list(check.filtered_out_of_scope)

    out_of_scope_block = _render_out_of_scope(out_of_scope_items)
    warnings_block = render_prompt_assembly_warnings(check.removed_items)

    # Builder-side enforcement copy only when the debate required edits.
    mandatory_edits_directive = ""
    if (
        recommendation == "APPROVE_WITH_MANDATORY_EDITS"
        and mandatory_edits
    ):
        mandatory_edits_directive = (
            "\nBUILDER DIRECTIVE — MANDATORY EDITS\n\n"
            "The debate approved this work item ONLY with the mandatory\n"
            "edits listed above. You MUST:\n"
            "- Treat the mandatory edits as IN SCOPE.\n"
            "- Do not skip, downgrade, rename, omit, or reinterpret any\n"
            "  mandatory edit.\n"
            "- Implement the approved Work Item plus every mandatory edit.\n"
            "- Do not mark this task complete until the mandatory edits\n"
            "  are addressed or explicitly blocked with operator sign-off.\n"
        )

    debate_outcome_block = _render_debate_outcome(
        recommendation=recommendation,
        implementation_readiness=implementation_readiness,
        debate_run_id=debate_run_id,
    )

    body = (
        f"PROMPT ID: LEGION-IMPLEMENT-WI-{work_item.id}-CARD-001\n"
        f"PROMPT TYPE: approved-implementation-merge-deploy\n"
        f"TARGET HOST: lgn-remote-01\n"
        f"TARGET REPO: {effective_target_repo}\n"
        f"TARGET PR: new PR only AFTER local pre-push review passes\n"
        f"TASK: {work_item.title}\n"
        f"EXPECTED OUTCOME: Implement approved work item according to debate outcome\n\n"
        f"SAFETY GATE — READ FIRST\n\n"
        f"You are working on lgn-remote-01.\n\n"
        f"Before doing any repo, git, Docker, or file mutation:\n"
        f"1. Verify hostname is lgn-remote-01.\n"
        f"2. Verify repo path is {effective_target_repo}.\n"
        f"3. Verify current branch and git status.\n"
        f"4. Preserve dirty work.\n"
        f"5. Do not modify Hermes source/config.\n"
        f"6. Do not modify Ollama hosts, ai-4080, LEGION, or model runtime configuration.\n"
        f"7. Do not expose API keys, provider secrets, prompts, private work item data, raw model prompts, or raw attachment contents in logs.\n"
        f"8. Do not hard-delete any history.\n"
        f"9. Do NOT push branch or create PR until local pre-push review passes.\n"
        f"10. Do not auto-approve or auto-implement without certification.\n\n"
        f"WORKTREE RULE\n\n"
        f"All repo, git, test, build, and Codex review operations for this\n"
        f"task MUST occur inside the target worktree below. The shared\n"
        f"operator/control worktree is reserved for operator/control work\n"
        f"only and must NOT be used as an implementation target by any\n"
        f"builder or reviewer process.\n\n"
        f"FAIL FAST: if `git rev-parse --show-toplevel` resolves to\n"
        f"{shared_repo_for_wi} you are in the wrong worktree. STOP, do\n"
        f"not modify state, and report the misrouted worktree path to the\n"
        f"operator. Do not auto-stash, auto-checkout, or auto-recover.\n\n"
        f"WORKTREE METADATA\n\n"
        f"- Target Worktree: {target_worktree or '(not assigned — using shared repo as legacy fallback; this is a configuration error)'}\n"
        f"- Feature Branch: {feature_branch or '(not assigned — orchestrator did not record the branch name)'}\n"
        f"- Base Ref: {chosen_base_ref or '(not recorded — orchestrator did not record the base ref)'}\n"
        f"- Shared Operator/Control Repo: {shared_repo_for_wi}\n"
        f"- Using Dedicated Worktree: {'yes' if using_worktree else 'NO'}\n\n"
        f"WORK ITEM DETAILS\n\n"
        f"- ID: {work_item.id}\n"
        f"- Type: {work_item.type}\n"
        f"- Priority: {work_item.priority}\n"
        f"- Target App: {work_item.target_app or 'N/A'}\n"
        f"- Source: {work_item.source}\n"
        f"- Acceptance Notes: {work_item.acceptance_notes or 'None provided'}\n\n"
        f"DEBATE OUTCOME\n\n"
        f"{debate_outcome_block}\n\n"
    )

    if mandatory_edits_block:
        body += mandatory_edits_block + "\n"

    body += (
        f"OUT OF SCOPE\n\n"
        f"{out_of_scope_block}\n"
    )

    if warnings_block:
        body += "\n" + warnings_block + "\n"

    if mandatory_edits_directive:
        body += "\n" + mandatory_edits_directive.lstrip("\n")

    body += (
        "\nLOCAL PRE-PUSH REVIEW GATE — MANDATORY\n\n"
        "Before pushing any branch or creating a PR, you MUST complete these steps locally:\n\n"
        "1. Create local implementation branch (do NOT push yet):\n"
        "   git checkout -b feature/<your-feature-name>\n\n"
        "2. Implement and commit locally:\n"
        "   git add <files>\n"
        "   git commit -m \"descriptive message\"\n\n"
        "3. Run deterministic checks:\n"
        "   git diff --check\n"
        f"   cd backend && .venv/bin/pytest tests/ -q\n"
        f"   cd frontend && npm run build\n\n"
        "4. Run local secret scan:\n"
        f"   /root/.hermes/LEGION_TOOLS/bin/legion-secret-scan --repo {effective_target_repo} --base <base-branch> --head <your-branch>\n\n"
        "5. Run local Codex review (NO PR REQUIRED):\n"
        "   /root/.hermes/LEGION_TOOLS/bin/legion-codex-local-review \\\n"
        f"     --repo {effective_target_repo} \\\n"
        "     --base <base-branch> \\\n"
        "     --head <your-branch> \\\n"
        f"     --report /root/.hermes/LEGION_TOOLS/LOCAL_CODEX_REVIEW_<WI_ID>.md\n\n"
        "6. Verify local review verdict:\n"
        "   - Must be APPROVE or APPROVE_WITH_NON_BLOCKING_NOTES\n"
        "   - If REQUEST_CHANGES or BLOCKED: fix issues, re-run gates, DO NOT PUSH\n\n"
        "7. ONLY AFTER local review passes:\n"
        "   git push origin <your-branch>\n"
        "   gh pr create --base <base-branch> --head <your-branch> ...\n\n"
        "WARNING: Pushing before local review passes may expose secrets or incomplete work.\n\n"
        "PHASE 1 — INSPECT\n\n"
        "Run:\n"
        "hostname\n"
        f"cd {effective_target_repo} || exit 1\n"
        "git status --short\n"
        "git branch --show-current\n"
        "git log -60 --oneline --decorate\n\n"
        "Preserve dirty work before making changes.\n\n"
        "PHASE 2 — IMPLEMENT\n\n"
        "Implement the approved scope according to:\n"
        "- Work item title and description\n"
        "- Acceptance notes\n"
        "- Mandatory edits (if APPROVE_WITH_MANDATORY_EDITS)\n"
        "- Original scope (if APPROVE_AS_IS)\n\n"
        "Do not downgrade or skip mandatory edits.\n\n"
        "PHASE 3 — TESTS\n\n"
        "Run:\n"
        "- git diff --check\n"
        "- backend tests\n"
        "- frontend build/test if present\n"
        "- docker compose config\n\n"
        "PHASE 4 — INDEPENDENT REVIEW\n\n"
        "Reviewer must use independent route from Builder.\n"
        "Do not certify if implementation and review used same backend/model/profile.\n\n"
        "PHASE 5 — COMMIT / MERGE / DEPLOY\n\n"
        "If validation passes and review is CERTIFIED:\n"
        "1. Commit with descriptive message\n"
        "2. Verify local pre-push review passed:\n"
        "   - git diff --check: clean\n"
        "   - Secret scan: PASSED\n"
        "   - Local Codex review: APPROVE or APPROVE_WITH_NON_BLOCKING_NOTES\n"
        "3. Push feature branch (ONLY if above checks pass)\n"
        "4. Open/update PR to main\n"
        "5. Merge automatically if clean\n"
        "6. Sync main\n"
        "7. Deploy if applicable\n"
        "8. Runtime verify\n\n"
        "PHASE 6 — REPORT\n\n"
        "Write final report to /root/.hermes/LEGION_TOOLS/ with implementation provenance.\n\n"
        "FINAL RESPONSE\n\n"
        "Return only the clean LEGION TASK RESULT block.\n"
    )
    return body


def _render_debate_outcome(
    recommendation: Optional[str],
    implementation_readiness: Optional[str],
    debate_run_id: Optional[int],
) -> str:
    """Render the DEBATE OUTCOME block, preserving the actual values."""
    return (
        f"- Recommendation: {recommendation or 'Not recorded'}\n"
        f"- Implementation Readiness: {implementation_readiness or 'Not recorded'}\n"
        f"- Debate Run ID: {debate_run_id if debate_run_id is not None else 'Not recorded'}"
    )


def _render_out_of_scope(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _resolve_shared_repo_path(work_item: WorkItem) -> str:
    """Return the shared operator/control repo path for ``work_item``.

    Mirrors :func:`app.worktree_paths.shared_repo_path_for_work_item`
    so the prompt body's "shared repo" reference and the safety gate
    agree on the same baseline.
    """
    target = getattr(work_item, "target_app", None)
    if target and "hub" in target.lower():
        return "/srv/repo/lgn-hub"
    return "/srv/repo/legion-dashboard"


def _is_under_worktrees(path: Optional[str]) -> bool:
    """Return True if ``path`` lives under ``/srv/worktrees/``."""
    if not path:
        return False
    norm = path.rstrip("/")
    return norm == "/srv/worktrees" or norm.startswith("/srv/worktrees/")
