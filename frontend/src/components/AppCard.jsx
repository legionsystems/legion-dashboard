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

function formatTimestamp(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toISOString().replace("T", " ").split(".")[0] + "Z";
}

function ActionButton({ action, onClick, disabled, busy }) {
  const variant = ACTION_VARIANTS[action] || "ghost";
  const label = action.toUpperCase();
  return (
    <Button
      variant={variant}
      size="sm"
      onClick={onClick}
      disabled={disabled}
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
  const lastResultColor =
    app.last_result === "success"
      ? "#10B981"
      : app.last_result === "failed"
      ? "#EF4444"
      : "#5A5A5A";

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
          </span>
        </div>
        <div className="grid grid-cols-[90px_1fr] gap-2">
          <span className="label-tel">LAST ACTION</span>
          <span className="font-mono text-fg-primary">
            {app.last_action || "—"}
            {app.last_result && (
              <span
                className="ml-2 font-mono uppercase tracking-telemetry text-[10px] font-semibold"
                style={{ color: lastResultColor }}
              >
                / {app.last_result}
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
        {actions.map((action) => (
          <ActionButton
            key={action}
            action={action}
            busy={busyAction === action}
            disabled={busyAction !== null && busyAction !== action}
            onClick={() => onAction?.(action)}
          />
        ))}
      </footer>
    </article>
  );
}
