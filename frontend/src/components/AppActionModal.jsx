import { useEffect } from "react";
import { Button } from "./buttons.jsx";

const DANGEROUS = new Set(["stop", "rebuild"]);

const ACTION_COPY = {
  start: {
    headline: "START APP",
    body: "Will run `docker compose up -d` against the configured compose file.",
    confirmVariant: "primary",
    confirmLabel: "START",
  },
  stop: {
    headline: "STOP APP",
    body: "Will run `docker compose down`. Running containers will be terminated and the app will be unreachable until restarted.",
    confirmVariant: "danger",
    confirmLabel: "STOP",
  },
  restart: {
    headline: "RESTART APP",
    body: "Will run `docker compose restart`. Brief downtime expected.",
    confirmVariant: "primary",
    confirmLabel: "RESTART",
  },
  pull: {
    headline: "PULL IMAGES",
    body: "Will run `docker compose pull`. No containers are restarted.",
    confirmVariant: "primary",
    confirmLabel: "PULL",
  },
  rebuild: {
    headline: "REBUILD APP",
    body: "Will run `docker compose up -d --build`. Images are rebuilt from source and containers replaced.",
    confirmVariant: "danger",
    confirmLabel: "REBUILD",
  },
  logs: {
    headline: "VIEW LOGS",
    body: "Will run `docker compose logs --tail 200` and stream the result here.",
    confirmVariant: "primary",
    confirmLabel: "FETCH LOGS",
  },
};

export default function AppActionModal({
  app,
  action,
  busy,
  onConfirm,
  onCancel,
}) {
  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onCancel?.();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  if (!action || !app) return null;
  const copy = ACTION_COPY[action] || {
    headline: action.toUpperCase(),
    body: `Will execute '${action}'.`,
    confirmVariant: "primary",
    confirmLabel: action.toUpperCase(),
  };
  const dangerous = DANGEROUS.has(action);

  return (
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center justify-center bg-canvas/80 backdrop-blur-sm p-4"
      onClick={onCancel}
    >
      <div
        className={`w-full max-w-md border ${
          dangerous ? "border-alert" : "border-edge-strong"
        } bg-surface shadow-inset`}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-headline"
      >
        <header
          className={`flex items-center justify-between gap-3 border-b px-4 py-3 ${
            dangerous ? "border-alert/60" : "border-edge"
          }`}
        >
          <div>
            <div
              className={`label-tel-strong ${
                dangerous ? "text-alert" : "text-fg-primary"
              }`}
            >
              [ {dangerous ? "DANGER" : "CONFIRM"} ]
            </div>
            <h2
              id="modal-headline"
              className="mt-1 font-display text-lg font-bold tracking-tight"
            >
              {copy.headline}
            </h2>
          </div>
          <button
            type="button"
            onClick={onCancel}
            className="font-mono text-fg-muted hover:text-fg-primary"
            aria-label="Close"
          >
            ✕
          </button>
        </header>

        <div className="px-4 py-4 space-y-3">
          <div className="border border-edge bg-canvas px-3 py-2">
            <div className="label-tel">TARGET</div>
            <div className="mt-1 font-mono text-sm text-fg-primary">
              {app.name}{" "}
              <span className="text-fg-muted">/ {app.app_id}</span>
            </div>
            <div className="mt-1 font-mono text-[11px] text-fg-secondary truncate">
              {app.compose_path}
            </div>
          </div>
          <p className="text-sm text-fg-secondary">{copy.body}</p>
          {dangerous && (
            <p className="text-xs font-mono uppercase tracking-telemetry text-alert">
              !! THIS ACTION IS DESTRUCTIVE OR DISRUPTIVE
            </p>
          )}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-edge px-4 py-3">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            CANCEL
          </Button>
          <Button
            variant={copy.confirmVariant}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "RUNNING…" : copy.confirmLabel}
          </Button>
        </footer>
      </div>
    </div>
  );
}
