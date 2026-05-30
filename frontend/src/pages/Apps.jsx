import { useCallback, useEffect, useState } from "react";
import { getJson, postJson } from "../api/client.js";
import AppCard from "../components/AppCard.jsx";
import AppActionModal from "../components/AppActionModal.jsx";
import { Panel } from "../components/panel.jsx";
import {
  EmptyState,
  ErrorBanner,
  SkeletonBlock,
} from "../components/states.jsx";
import { Button } from "../components/buttons.jsx";

function summariseStatuses(apps) {
  const counts = { running: 0, stopped: 0, unknown: 0 };
  for (const a of apps) {
    if (counts[a.status] !== undefined) counts[a.status] += 1;
    else counts.unknown += 1;
  }
  return counts;
}

// Banner colour palette by structured result type. Mirrors backend result
// classification: success / not_running / not_applicable / not_found /
// failed / timeout. Use neutral tones for benign no-ops so they don't read
// as errors.
const FEEDBACK_THEMES = {
  success: {
    border: "border-st-completed/60",
    bg: "bg-st-completed/10",
    text: "text-st-completed",
    glyph: "✓",
  },
  not_running: {
    border: "border-edge-strong",
    bg: "bg-raised",
    text: "text-fg-secondary",
    glyph: "○",
  },
  not_applicable: {
    border: "border-edge-strong",
    bg: "bg-raised",
    text: "text-fg-secondary",
    glyph: "—",
  },
  not_found: {
    border: "border-st-review/60",
    bg: "bg-st-review/10",
    text: "text-st-review",
    glyph: "?",
  },
  failed: {
    border: "border-alert/60",
    bg: "bg-alert/10",
    text: "text-alert",
    glyph: "!!",
  },
  timeout: {
    border: "border-alert/60",
    bg: "bg-alert/10",
    text: "text-alert",
    glyph: "⏱",
  },
};

function feedbackTheme(result) {
  return FEEDBACK_THEMES[result] || FEEDBACK_THEMES.failed;
}

export default function Apps() {
  const [apps, setApps] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState({ appId: null, action: null });
  const [busy, setBusy] = useState(false);
  const [actionFeedback, setActionFeedback] = useState(null);
  const [logsPanel, setLogsPanel] = useState(null); // { appId, lines, result, message }

  const refresh = useCallback(() => {
    let cancelled = false;
    setError(null);
    getJson("/apps")
      .then((data) => {
        if (!cancelled) setApps(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    return refresh();
  }, [refresh]);

  function openAction(app, action) {
    setActionFeedback(null);
    setPending({ appId: app.app_id, action });
  }

  function closeAction() {
    if (busy) return;
    setPending({ appId: null, action: null });
  }

  function pendingApp() {
    if (!pending.appId || !apps) return null;
    return apps.find((a) => a.app_id === pending.appId) || null;
  }

  async function confirmAction() {
    const target = pendingApp();
    if (!target || !pending.action) return;
    setBusy(true);
    setActionFeedback(null);
    try {
      const result = await postJson(
        `/apps/${target.app_id}/action`,
        { action: pending.action },
      );
      setApps((prev) =>
        prev
          ? prev.map((a) => (a.app_id === result.app.app_id ? result.app : a))
          : prev,
      );
      setActionFeedback({
        appId: target.app_id,
        action: pending.action,
        result: result.log.result,
        exitCode: result.log.exit_code,
        message: result.log.message,
      });

      // Only fetch the logs panel when the logs action actually produced
      // running containers — otherwise the structured result already carries
      // the message the operator needs.
      if (pending.action === "logs" && result.log.result === "success") {
        try {
          const logs = await getJson(`/apps/${target.app_id}/logs?tail=200`);
          setLogsPanel({
            appId: target.app_id,
            lines: logs.lines,
            result: logs.result,
            message: logs.message,
          });
        } catch (err) {
          setError(err.message);
        }
      } else if (pending.action === "logs") {
        // Surface the structured outcome in the logs panel itself so the
        // operator gets a single coherent place to read it.
        setLogsPanel({
          appId: target.app_id,
          lines: [],
          result: result.log.result,
          message: result.log.message,
        });
      }
    } catch (err) {
      setError(err.message);
      setActionFeedback({
        appId: target.app_id,
        action: pending.action,
        result: "failed",
        exitCode: null,
        message: err.message,
      });
    } finally {
      setBusy(false);
      setPending({ appId: null, action: null });
    }
  }

  const counts = apps ? summariseStatuses(apps) : null;

  return (
    <div className="space-y-5">
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3">
        <div>
          <div className="label-tel">OPERATIONS / APPS</div>
          <h1
            className="font-display font-extrabold tracking-tighter-display leading-none mt-1"
            style={{ fontSize: "clamp(2rem, 5vw, 3rem)" }}
          >
            APPS
          </h1>
          <p className="mt-2 text-sm text-fg-secondary max-w-xl">
            Managed LEGION services. Start, stop, pull, restart, and rebuild
            via allowlisted docker compose actions. Every action is logged.
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Button variant="ghost" onClick={refresh}>
            ↻ REFRESH
          </Button>
        </div>
      </div>

      <ErrorBanner message={error} />

      {actionFeedback && (
        <ActionFeedbackBanner feedback={actionFeedback} />
      )}

      {apps === null && !error && (
        <Panel title="LOADING">
          <SkeletonBlock rows={6} />
        </Panel>
      )}

      {apps && apps.length === 0 && (
        <EmptyState
          title="NO APPS CONFIGURED"
          hint="No apps are registered in the control plane. Add entries to backend/app/apps_config.py and restart."
        />
      )}

      {apps && apps.length > 0 && (
        <>
          <div className="grid grid-cols-3 gap-px bg-edge border border-edge">
            <div className="bg-surface px-3 py-2">
              <div className="label-tel">RUNNING</div>
              <div
                className="mt-0.5 font-mono text-2xl font-bold tabular-nums"
                style={{ color: "#F59E0B" }}
              >
                {counts?.running ?? 0}
              </div>
            </div>
            <div className="bg-surface px-3 py-2">
              <div className="label-tel">STOPPED</div>
              <div className="mt-0.5 font-mono text-2xl font-bold tabular-nums text-fg-secondary">
                {counts?.stopped ?? 0}
              </div>
            </div>
            <div className="bg-surface px-3 py-2">
              <div className="label-tel">UNKNOWN</div>
              <div className="mt-0.5 font-mono text-2xl font-bold tabular-nums text-fg-muted">
                {counts?.unknown ?? 0}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
            {apps.map((app) => (
              <AppCard
                key={app.app_id}
                app={app}
                busyAction={
                  busy && pending.appId === app.app_id ? pending.action : null
                }
                onAction={(action) => openAction(app, action)}
              />
            ))}
          </div>
        </>
      )}

      {logsPanel && (
        <Panel
          title={`LOGS / ${logsPanel.appId}`}
          subtitle={`// ${logsPanel.lines.length} LINES`}
          right={
            <button
              type="button"
              onClick={() => setLogsPanel(null)}
              className="font-mono uppercase tracking-telemetry text-[11px] font-semibold text-fg-secondary hover:text-fg-primary"
            >
              CLOSE ×
            </button>
          }
        >
          <LogsPanelBody panel={logsPanel} />
        </Panel>
      )}

      <AppActionModal
        app={pendingApp()}
        action={pending.action}
        busy={busy}
        result={actionFeedback}
        onConfirm={confirmAction}
        onCancel={closeAction}
      />
    </div>
  );
}

function ActionFeedbackBanner({ feedback }) {
  const theme = feedbackTheme(feedback.result);
  return (
    <div
      className={`flex items-start gap-3 border px-3 py-2 text-sm ${theme.border} ${theme.bg} ${theme.text}`}
      role="status"
    >
      <span className="font-mono mt-0.5">{theme.glyph}</span>
      <div className="flex-1 min-w-0">
        <div className="font-mono uppercase tracking-telemetry text-[11px] font-semibold">
          {feedback.action} / {feedback.appId} / {feedback.result}
        </div>
        {feedback.message && (
          <div className="mt-0.5 text-[12px] font-mono opacity-90 break-words">
            {feedback.message}
          </div>
        )}
      </div>
    </div>
  );
}

function LogsPanelBody({ panel }) {
  if (panel.result && panel.result !== "success") {
    const theme = feedbackTheme(panel.result);
    return (
      <div
        className={`border px-3 py-3 ${theme.border} ${theme.bg} ${theme.text}`}
      >
        <div className="font-mono uppercase tracking-telemetry text-[11px] font-semibold">
          {theme.glyph} {panel.result}
        </div>
        {panel.message && (
          <div className="mt-1 font-mono text-[12px] opacity-90 break-words">
            {panel.message}
          </div>
        )}
      </div>
    );
  }
  if (panel.lines.length === 0) {
    return <div className="label-tel">NO LOG OUTPUT</div>;
  }
  return (
    <pre className="max-h-96 overflow-auto bg-canvas border border-edge p-3 font-mono text-[11px] leading-relaxed text-fg-secondary whitespace-pre-wrap">
      {panel.lines.join("\n")}
    </pre>
  );
}
