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

export default function Apps() {
  const [apps, setApps] = useState(null);
  const [error, setError] = useState(null);
  const [pending, setPending] = useState({ appId: null, action: null });
  const [busy, setBusy] = useState(false);
  const [actionFeedback, setActionFeedback] = useState(null);
  const [logsPanel, setLogsPanel] = useState(null); // { appId, lines }

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
      });

      if (pending.action === "logs") {
        try {
          const logs = await getJson(`/apps/${target.app_id}/logs?tail=200`);
          setLogsPanel({ appId: target.app_id, lines: logs.lines });
        } catch (err) {
          setError(err.message);
        }
      }
    } catch (err) {
      setError(err.message);
      setActionFeedback({
        appId: target.app_id,
        action: pending.action,
        result: "failed",
        exitCode: null,
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
        <div
          className={`flex items-center gap-3 border px-3 py-2 text-sm ${
            actionFeedback.result === "success"
              ? "border-st-completed/60 bg-st-completed/10 text-st-completed"
              : "border-alert/60 bg-alert/10 text-alert"
          }`}
        >
          <span className="font-mono">
            {actionFeedback.result === "success" ? "✓" : "!!"}
          </span>
          <span className="font-mono uppercase tracking-telemetry text-[11px] font-semibold">
            {actionFeedback.action} / {actionFeedback.appId} /{" "}
            {actionFeedback.result}
            {actionFeedback.exitCode !== null && (
              <span className="ml-2 opacity-70">
                exit={actionFeedback.exitCode}
              </span>
            )}
          </span>
        </div>
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
          {logsPanel.lines.length === 0 ? (
            <div className="label-tel">NO LOG OUTPUT</div>
          ) : (
            <pre className="max-h-96 overflow-auto bg-canvas border border-edge p-3 font-mono text-[11px] leading-relaxed text-fg-secondary whitespace-pre-wrap">
              {logsPanel.lines.join("\n")}
            </pre>
          )}
        </Panel>
      )}

      <AppActionModal
        app={pendingApp()}
        action={pending.action}
        busy={busy}
        onConfirm={confirmAction}
        onCancel={closeAction}
      />
    </div>
  );
}
