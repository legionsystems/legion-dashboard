import { useEffect, useState } from "react";
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

const PHASE_LABELS = {
  queued: { label: "QUEUED", color: "#6B7280" },
  validating_docker: { label: "VALIDATING", color: "#3B82F6" },
  running_compose: { label: "RUNNING", color: "#8B5CF6" },
  collecting_output: { label: "COLLECTING", color: "#F59E0B" },
  completed: { label: "DONE", color: "#10B981" },
  failed: { label: "FAILED", color: "#EF4444" },
  timed_out: { label: "TIMEOUT", color: "#EF4444" },
};

function formatElapsed(seconds) {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default function AppActionModal({
  app,
  action,
  busy,
  result,
  onConfirm,
  onCancel,
}) {
  const [showOutput, setShowOutput] = useState(false);
  const [elapsed, setElapsed] = useState(null);
  const [phase, setPhase] = useState(null);

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onCancel?.();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  // Auto-expand output for logs action
  useEffect(() => {
    if (action === "logs" && result?.stdout_tail) {
      setShowOutput(true);
    } else {
      setShowOutput(false);
    }
  }, [action, result]);

  // Poll for progress during long-running actions
  useEffect(() => {
    if (!busy || !app?.app_id) return;

    let polling = true;
    const poll = async () => {
      try {
        const res = await fetch(`/api/apps/${app.app_id}/actions/latest`);
        if (!res.ok) return;
        const data = await res.json();
        if (polling) {
          setPhase(data.phase || null);
          setElapsed(data.elapsed_seconds || null);
        }
      } catch {
        // Ignore poll errors
      }
    };

    poll();
    const interval = setInterval(poll, 1000);
    return () => {
      polling = false;
      clearInterval(interval);
    };
  }, [busy, app?.app_id]);

  if (!action || !app) return null;
  const copy = ACTION_COPY[action] || {
    headline: action.toUpperCase(),
    body: `Will execute '${action}'.`,
    confirmVariant: "primary",
    confirmLabel: action.toUpperCase(),
  };
  const dangerous = DANGEROUS.has(action);

  const phaseInfo = phase ? PHASE_LABELS[phase] : null;
  const hasOutput = result?.stdout_tail || result?.stderr_tail;

  return (
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center justify-center bg-canvas/80 backdrop-blur-sm p-4"
      onClick={onCancel}
    >
      <div
        className={`w-full max-w-lg border ${
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

          {/* Progress indicator for long-running actions */}
          {busy && (
            <div className="border border-edge bg-canvas px-3 py-2">
              <div className="flex items-center justify-between">
                <div className="label-tel">STATUS</div>
                {phaseInfo && (
                  <span
                    className="font-mono text-[10px] tracking-telemetry font-semibold px-1.5 py-0.5 rounded"
                    style={{
                      backgroundColor: `${phaseInfo.color}20`,
                      color: phaseInfo.color,
                      border: `1px solid ${phaseInfo.color}55`,
                    }}
                  >
                    {phaseInfo.label}
                  </span>
                )}
              </div>
              <div className="mt-2 flex items-center gap-3">
                {busy && (
                  <span className="animate-spin text-fg-primary" aria-hidden="true">
                    ⟳
                  </span>
                )}
                <div className="font-mono text-xs text-fg-secondary">
                  {elapsed != null ? `Running for ${formatElapsed(elapsed)}` : "Starting..."}
                </div>
              </div>
            </div>
          )}

          <p className="text-sm text-fg-secondary">{copy.body}</p>
          {dangerous && (
            <p className="text-xs font-mono uppercase tracking-telemetry text-alert">
              !! THIS ACTION IS DESTRUCTIVE OR DISRUPTIVE
            </p>
          )}

          {/* Collapsible technical output */}
          {hasOutput && (
            <div className="border border-edge bg-canvas rounded">
              <button
                type="button"
                onClick={() => setShowOutput(!showOutput)}
                className="w-full flex items-center justify-between px-3 py-2 text-xs font-mono text-fg-secondary hover:text-fg-primary transition-colors"
              >
                <span>
                  {showOutput ? "Hide technical output" : "Show technical output"}
                </span>
                <span>{showOutput ? "▲" : "▼"}</span>
              </button>
              {showOutput && (
                <div className="border-t border-edge px-3 py-2 max-h-64 overflow-auto">
                  {result.stdout_tail && (
                    <pre className="font-mono text-[10px] text-fg-secondary whitespace-pre-wrap break-all">
                      {result.stdout_tail}
                    </pre>
                  )}
                  {result.stderr_tail && (
                    <pre className="font-mono text-[10px] text-alert whitespace-pre-wrap break-all mt-2">
                      {result.stderr_tail}
                    </pre>
                  )}
                </div>
              )}
            </div>
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
