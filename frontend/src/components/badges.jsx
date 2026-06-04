// STATUS_META covers both raw Kanban statuses and the lifecycle
// effective_state values produced by ``backend/app/lifecycle.py``. The list
// page renders ``effective_state`` in the Work Status column, so every value
// returned by ``compute_effective_state`` needs an entry here. "completed"
// is reserved for Work Item lifecycle completion (slice 5 ``complete``); the
// raw Kanban "completed" status is rendered as ``IMPLEMENTED`` to avoid the
// debated-but-no-build confusion called out in WI-32.
const STATUS_META = {
  // Raw Kanban statuses
  draft:           { color: "#6B7280", label: "DRAFT" },
  active:          { color: "#F59E0B", label: "ACTIVE" },
  running:         { color: "#F59E0B", label: "RUNNING" },
  stopped:         { color: "#6B7280", label: "STOPPED" },
  unknown:         { color: "#5A5A5A", label: "UNKNOWN" },
  review_needed:   { color: "#F97316", label: "REVIEW NEEDED" },
  pr_open:         { color: "#6366F1", label: "PR OPEN" },
  ready_for_merge: { color: "#06B6D4", label: "READY MERGE" },
  completed:       { color: "#10B981", label: "COMPLETED" },

  // Lifecycle effective_state values (see backend/app/lifecycle.py)
  drafting:                 { color: "#6B7280", label: "DRAFTING" },
  debating:                 { color: "#3B82F6", label: "DEBATING" },
  debated:                  { color: "#A855F7", label: "DEBATED" },
  approved:                 { color: "#3B82F6", label: "APPROVED" },
  building:                 { color: "#F59E0B", label: "BUILDING" },
  implemented:              { color: "#FACC15", label: "IMPLEMENTED" },
  in_review:                { color: "#6366F1", label: "REVIEW REQUIRED" },
  code_reviewed:            { color: "#06B6D4", label: "CODE REVIEWED" },
  changes_requested:        { color: "#F97316", label: "CHANGES REQUESTED" },
  review_failed:            { color: "#EF4444", label: "REVIEW FAILED" },
  preview_pending:          { color: "#F59E0B", label: "PREVIEW PENDING" },
  preview_ready:            { color: "#06B6D4", label: "PREVIEW READY" },
  needs_rework:             { color: "#F97316", label: "NEEDS REWORK" },
  certified:                { color: "#14B8A6", label: "CERTIFIED" },
  ready_to_merge:           { color: "#06B6D4", label: "READY TO MERGE" },
  merged:                   { color: "#22C55E", label: "MERGED" },
  merged_deployment_failed: { color: "#EF4444", label: "DEPLOY FAILED" },
  blocked_merge:            { color: "#EF4444", label: "MERGE BLOCKED" },
  blocked:                  { color: "#EF4444", label: "BLOCKED" },
  rejected:                 { color: "#8A8A8A", label: "REJECTED" },
  archived:                 { color: "#5A5A5A", label: "ARCHIVED" },
  complete:                 { color: "#10B981", label: "COMPLETE" },
};

const TYPE_META = {
  idea:   { color: "#EAB308", glyph: "*" },
  bug:    { color: "#EF4444", glyph: "!" },
  change: { color: "#06B6D4", glyph: "~" },
  note:   { color: "#8A8A8A", glyph: "=" },
  task:   { color: "#3B82F6", glyph: ">" },
  slice:  { color: "#A855F7", glyph: "#" },
};

const PRIORITY_META = {
  low:      { color: "#6B7280", label: "LOW" },
  medium:   { color: "#3B82F6", label: "MEDIUM" },
  high:     { color: "#F97316", label: "HIGH" },
  critical: { color: "#EF4444", label: "CRITICAL" },
};

const SOURCE_META = {
  operator: { color: "#EAEAEA", label: "OPERATOR" },
  builder:  { color: "#3B82F6", label: "BUILDER" },
  reviewer: { color: "#A855F7", label: "REVIEWER" },
  user:     { color: "#14B8A6", label: "USER" },
};

export function statusMeta(status) {
  return STATUS_META[status] || { color: "#5A5A5A", label: (status || "—").toUpperCase() };
}

export function typeMeta(type) {
  return TYPE_META[type] || { color: "#5A5A5A", glyph: "?" };
}

export function priorityMeta(priority) {
  return PRIORITY_META[priority] || { color: "#5A5A5A", label: (priority || "—").toUpperCase() };
}

export function sourceMeta(source) {
  return SOURCE_META[source] || { color: "#5A5A5A", label: (source || "—").toUpperCase() };
}

// The set of raw Kanban statuses the form's status picker exposes. Includes
// every value the backend can persist into ``WorkItem.status`` so an existing
// item never opens with an unselectable status value. ``debated`` is written
// by the debate executor (backend/app/debate_executor.py); ``certified`` is
// surfaced as a snapshot label by older flows. Excludes runtime app statuses
// (``running``/``stopped``/``unknown``) and the derived lifecycle states.
const _KANBAN_STATUSES = [
  "draft",
  "debated",
  "approved",
  "active",
  "review_needed",
  "pr_open",
  "ready_for_merge",
  "merged",
  "blocked",
  "certified",
  "completed",
];
export const ALL_STATUSES = _KANBAN_STATUSES;

// Lifecycle filter buckets shown above the Work Item list. Each bucket maps
// to a set of effective_state values; see WorkItemList for the mapping.
export const LIFECYCLE_FILTERS = [
  { key: "active", label: "ACTIVE", color: "#F59E0B" },
  { key: "blocked", label: "BLOCKED", color: "#EF4444" },
  { key: "review_required", label: "REVIEW REQUIRED", color: "#6366F1" },
  { key: "parked", label: "PARKED", color: "#8A8A8A" },
  { key: "completed", label: "COMPLETED", color: "#10B981" },
  { key: "all", label: "ALL", color: "#EAEAEA" },
];
export const ALL_TYPES = Object.keys(TYPE_META);
export const INTAKE_TYPES = ["idea", "bug", "change", "task", "slice"];
export const ALL_PRIORITIES = Object.keys(PRIORITY_META);
export const ALL_SOURCES = Object.keys(SOURCE_META);

export function StatusBadge({ status, size = "sm" }) {
  const meta = statusMeta(status);
  const pad = size === "xs" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-[11px]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 border ${pad} font-mono uppercase tracking-telemetry font-semibold`}
      style={{
        color: meta.color,
        borderColor: `${meta.color}55`,
        backgroundColor: `${meta.color}14`,
      }}
    >
      <span
        className="inline-block h-1.5 w-1.5"
        style={{ backgroundColor: meta.color }}
      />
      {meta.label}
    </span>
  );
}

export function TypeBadge({ type, size = "sm" }) {
  const meta = typeMeta(type);
  const pad = size === "xs" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-[11px]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 border ${pad} font-mono uppercase tracking-telemetry font-semibold`}
      style={{
        color: meta.color,
        borderColor: `${meta.color}55`,
        backgroundColor: "transparent",
      }}
    >
      <span className="opacity-70">{meta.glyph}</span>
      {(type || "—").toUpperCase()}
    </span>
  );
}

export function PriorityBadge({ priority, size = "sm" }) {
  const meta = priorityMeta(priority);
  const pad = size === "xs" ? "px-1.5 py-0.5 text-[10px]" : "px-2 py-0.5 text-[11px]";
  return (
    <span
      className={`inline-flex items-center gap-1.5 border ${pad} font-mono uppercase tracking-telemetry font-semibold`}
      style={{
        color: meta.color,
        borderColor: `${meta.color}55`,
        backgroundColor: `${meta.color}10`,
      }}
    >
      {meta.label}
    </span>
  );
}
