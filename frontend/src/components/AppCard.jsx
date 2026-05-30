import { StatusBadge } from "./badges.jsx";
import { Button } from "./buttons.jsx";

const ACTION_VARIANTS = {
  start: "primary",
  pull: "ghost",
  restart: "ghost",
  logs: "ghost",
  stop: "danger",
  rebuild: "danger",
};

const ACTION_GLYPHS = {
  start: "▶",
  stop: "■",
  restart: "↻",
  pull: "↓",
  rebuild: "⚙",
  logs: "≡",
};

// Visual category for last_result; mirrors the structured result types the
// backend now returns. Keep in sync with apps.py result classification.
const RESULT_META = {
  success:        { color: "#10B981", label: "OK" },
  not_running:    { color: "#8A8A8A", label: "IDLE" },
  not_applicable: { color: "#8A8A8A", label: "N/A" },
  not_found:      { color: "#F97316", label: "MISSING" },
  failed:         { color: "#EF4444", label: "FAILED" },
  timeout:        { color: "#EF4444", label: "TIMEOUT" },
  pending:        { color: "#5A5A5A", label: "PENDING" },
};

function resultMeta(result) {
  return RESULT_META[result] || {
    color: "#5A5A5A",
    label: (result || "—").toUpperCase(),
  };
}

function formatTimestamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toISOString().replace("T", " ").split(".")[0] + "Z";
}

function ActionButton({ action, onClick, disabled, busy, title }) {
  const variant = ACTION_VARIANTS[action] || "ghost";
  const label = action.toUpperCase();
  return (
    <Button
      variant={variant}
      size="sm"
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      <span className="font-mono opacity-70">{ACTION_GLYPHS[action]}</span>
      {busy ? "…" : label}
    </Button>
  );
}

export default function AppCard({
  app,
  actions = ["start", "stop", "restart", "pull", "rebuild", "logs"],
  busyAction = null,
  onAction,
}) {
  const meta = resultMeta(app.last_result);
  const buildOnly = app.build_only === true;
  const composeMissing = app.compose_exists === false;

  // Open/View link: only shown when exactly one web port is detected
  const canOpen = app.can_open === true && app.web_url;
  const openLabel = app.web_port ? `Open (${app.web_port})` : "Open";

  return (
    <article className="border border-edge bg-surface flex flex-col">
      <header className="flex items-start justify-between gap-3 border-b border-edge px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
              [ APP ]
            </span>
            <span className="font-mono text-[10px] tracking-telemetry text-fg-secondary">
              {app.app_id}
            </span>
            {buildOnly && (
              <span
                className="font-mono text-[10px] tracking-telemetry font-semibold border px-1.5 py-0.5"
                style={{
                  color: "#06B6D4",
                  borderColor: "#06B6D455",
                  backgroundColor: "#06B6D414",
                }}
                title="This app is built locally; `pull` is not applicable."
              >
                BUILD
              </span>
            )}
          </div>
          <h3 className="mt-1 font-display text-lg font-bold tracking-tight truncate">
            {app.name}
          </h3>
        </div>
        <StatusBadge status={app.status} />
      </header>

      <div className="px-4 py-3 space-y-2 text-xs flex-1">
        <div className="grid grid-cols-[90px_1fr] gap-2">
          <span className="label-tel">REPO</span>
          <span className="font-mono text-fg-secondary truncate">
            {app.repo || "—"}
          </span>
        </div>
        <div className="grid grid-cols-[90px_1fr] gap-2">
          <span className="label-tel">COMPOSE</span>
          <span className="font-mono text-fg-secondary truncate">
            {app.compose_path}
            {composeMissing && (
              <span
                className="ml-2 font-mono uppercase tracking-telemetry text-[10px] font-semibold"
                style={{ color: "#F97316" }}
                title="Compose file not found on disk"
              >
                / MISSING
              </span>
            )}
          </span>
        </div>
        <div className="grid grid-cols-[90px_1fr] gap-2">
          <span className="label-tel">LAST ACTION</span>
          <span className="font-mono text-fg-primary">
            {app.last_action || "—"}
            {app.last_result && (
              <span
                className="ml-2 font-mono uppercase tracking-telemetry text-[10px] font-semibold"
                style={{ color: meta.color }}
              >
                / {meta.label}
              </span>
            )}
          </span>
        </div>
        <div className="grid grid-cols-[90px_1fr] gap-2">
          <span className="label-tel">UPDATED</span>
          <span className="font-mono text-fg-muted tabular-nums">
            {formatTimestamp(app.last_updated)}
          </span>
        </div>
      </div>

      <footer className="border-t border-edge px-4 py-3 flex flex-wrap gap-1.5">
        {actions.map((action) => {
          const isPull = action === "pull";
          const pullDisabled = isPull && buildOnly;
          const composeDisabled = composeMissing && action !== "logs";
          const busy = busyAction === action;
          const lockedByOther = busyAction !== null && busyAction !== action;
          const disabled = pullDisabled || composeDisabled || lockedByOther;
          let title;
          if (pullDisabled) {
            title = "App is built locally; pull is not applicable. Use REBUILD.";
          } else if (composeDisabled) {
            title = "Compose file is missing on disk; actions are unavailable.";
          }
          return (
            <ActionButton
              key={action}
              action={action}
              busy={busy}
              disabled={disabled}
              title={title}
              onClick={() => onAction?.(action)}
            />
          );
        })}
        {/* Open/View link: only shown when exactly one web port is detected */}
        {canOpen && (
          <a
            href={app.web_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-mono font-semibold border rounded transition-colors"
            style={{
              borderColor: "#10B98155",
              backgroundColor: "#10B98114",
              color: "#10B981",
            }}
            title={`Open app at ${app.web_url}`}
          >
            <span>🔗</span>
            {openLabel}
          </a>
        )}
      </footer>
    </article>
  );
}
