const STATUS_META = {
  draft:           { color: "#6B7280", label: "DRAFT" },
  debated:         { color: "#A855F7", label: "DEBATED" },
  approved:        { color: "#3B82F6", label: "APPROVED" },
  active:          { color: "#F59E0B", label: "ACTIVE" },
  running:         { color: "#F59E0B", label: "RUNNING" },
  review_needed:   { color: "#F97316", label: "REVIEW" },
  certified:       { color: "#14B8A6", label: "CERTIFIED" },
  pr_open:         { color: "#6366F1", label: "PR OPEN" },
  ready_for_merge: { color: "#06B6D4", label: "READY MERGE" },
  merged:          { color: "#22C55E", label: "MERGED" },
  blocked:         { color: "#EF4444", label: "BLOCKED" },
  completed:       { color: "#10B981", label: "COMPLETED" },
};

const TYPE_META = {
  idea:  { color: "#EAB308", glyph: "*" },
  bug:   { color: "#EF4444", glyph: "!" },
  note:  { color: "#8A8A8A", glyph: "=" },
  task:  { color: "#3B82F6", glyph: ">" },
  slice: { color: "#A855F7", glyph: "#" },
};

export function statusMeta(status) {
  return STATUS_META[status] || { color: "#5A5A5A", label: (status || "—").toUpperCase() };
}

export function typeMeta(type) {
  return TYPE_META[type] || { color: "#5A5A5A", glyph: "?" };
}

export const ALL_STATUSES = Object.keys(STATUS_META).filter((k) => k !== "running");
export const ALL_TYPES = Object.keys(TYPE_META);

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
