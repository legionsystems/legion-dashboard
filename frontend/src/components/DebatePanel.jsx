import { useEffect, useMemo, useRef, useState } from "react";
import { getJson, postJson } from "../api/client.js";
import { Panel } from "./panel.jsx";
import { Button } from "./buttons.jsx";
import { ErrorBanner, EmptyState, SkeletonBlock } from "./states.jsx";

const DEBATE_MIN_ROUNDS = 1;
const DEBATE_MAX_ROUNDS = 5;

const RUN_STATUS_META = {
  queued: { color: "#F59E0B", label: "QUEUED" },
  running: { color: "#3B82F6", label: "RUNNING" },
  completed: { color: "#10B981", label: "COMPLETED" },
  failed: { color: "#EF4444", label: "FAILED" },
};

const RECOMMENDATION_META = {
  APPROVE_AS_IS: { color: "#10B981", label: "APPROVE AS IS" },
  APPROVE_WITH_EDITS: { color: "#06B6D4", label: "APPROVE WITH EDITS" },
  SPLIT_FIRST: { color: "#A855F7", label: "SPLIT FIRST" },
  NEEDS_MORE_DETAIL: { color: "#F97316", label: "NEEDS MORE DETAIL" },
  DO_NOT_BUILD_NOW: { color: "#EF4444", label: "DO NOT BUILD NOW" },
};

const READINESS_META = {
  READY: { color: "#10B981", label: "READY" },
  READY_AFTER_EDITS: { color: "#F59E0B", label: "READY AFTER EDITS" },
  NOT_READY: { color: "#EF4444", label: "NOT READY" },
};

const SIDE_META = {
  pro: { color: "#10B981", label: "PRO" },
  con: { color: "#EF4444", label: "CON" },
  neutral: { color: "#8A8A8A", label: "NEUTRAL" },
  arbiter: { color: "#A855F7", label: "ARBITER" },
};

const STANCE_OPTIONS = [
  { value: "auto_assign", label: "AUTO-ASSIGN" },
  { value: "pro", label: "PRO" },
  { value: "con", label: "CON" },
  { value: "neutral", label: "NEUTRAL" },
];

const VIEW_MODES = [
  { value: "chronological", label: "CHRONOLOGICAL" },
  { value: "side_by_side", label: "SIDE BY SIDE" },
];

function formatTime(iso) {
  if (!iso) return null;
  const tz = window.LEGION_DISPLAY_TIMEZONE || 'Australia/Sydney';
  try {
    const date = new Date(iso);
    return date.toLocaleString('en-AU', {
      timeZone: tz,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }).replace(',', '');
  } catch (e) {
    return date.toLocaleString('en-AU');
  }
}

function formatDuration(ms) {
  if (!ms || ms <= 0) return null;
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  if (minutes > 0) {
    return `${minutes}m ${secs}s`;
  }
  return `${seconds}s`;
}

function StatusChip({ kind, value }) {
  const map =
    kind === "status"
      ? RUN_STATUS_META
      : kind === "recommendation"
      ? RECOMMENDATION_META
      : kind === "readiness"
      ? READINESS_META
      : kind === "side"
      ? SIDE_META
      : {};
  const meta = map[value] || { color: "#5A5A5A", label: (value || "—").toUpperCase() };
  return (
    <span
      className="inline-flex items-center gap-1.5 border px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold"
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

function ArgumentBlock({ argument }) {
  let respondsTo = [];
  if (argument.responds_to_claim_ids) {
    if (Array.isArray(argument.responds_to_claim_ids)) {
      respondsTo = argument.responds_to_claim_ids;
    } else if (typeof argument.responds_to_claim_ids === 'string') {
      try {
        const parsed = JSON.parse(argument.responds_to_claim_ids);
        respondsTo = Array.isArray(parsed) ? parsed : [parsed];
      } catch (e) {
        respondsTo = [argument.responds_to_claim_ids];
      }
    }
  }
  
  if (!Array.isArray(respondsTo)) {
    respondsTo = [];
  }
  
  return (
    <div className="border border-edge/60 bg-canvas px-3 py-2">
      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
        <StatusChip kind="side" value={argument.side} />
        <span className="font-mono text-[10px] tracking-telemetry text-fg-secondary">
          {argument.role?.toUpperCase()}
        </span>
        <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
          R{argument.round_number}
        </span>
        {argument.claim_id && (
          <span className="font-mono text-[9px] tracking-tighter text-fg-muted px-1.5 py-0.5 border border-edge rounded">
            {argument.claim_id}
          </span>
        )}
      </div>
      
      {respondsTo.length > 0 && (
        <div className="mb-2 flex flex-wrap gap-1">
          <span className="font-mono text-[9px] text-fg-muted uppercase tracking-tight">Responds to:</span>
          {respondsTo.map(id => (
            <span key={id} className="font-mono text-[9px] text-[#3B82F6] bg-[#3B82F6]10 px-1.5 py-0.5 rounded">
              {id}
            </span>
          ))}
        </div>
      )}
      
      {(argument.concession || argument.rebuttal || argument.revised_position) && (
        <div className="mb-2 grid grid-cols-1 gap-1">
          {argument.concession && (
            <div className="text-xs">
              <span className="font-mono text-[9px] text-[#F59E0B] uppercase tracking-tight">Concession: </span>
              <span className="text-fg-secondary italic">{argument.concession}</span>
            </div>
          )}
          {argument.rebuttal && (
            <div className="text-xs">
              <span className="font-mono text-[9px] text-[#EF4444] uppercase tracking-tight">Rebuttal: </span>
              <span className="text-fg-secondary">{argument.rebuttal}</span>
            </div>
          )}
          {argument.revised_position && (
            <div className="text-xs">
              <span className="font-mono text-[9px] text-[#10B981] uppercase tracking-tight">Revised: </span>
              <span className="text-fg-secondary italic">{argument.revised_position}</span>
            </div>
          )}
        </div>
      )}
      
      <p className="text-sm text-fg-primary whitespace-pre-wrap leading-relaxed">
        {argument.content}
      </p>
    </div>
  );
}

function SideBySideView({ grouped }) {
  if (!grouped) return null;

  // Group arguments by round within each side for cleaner display
  const groupByRound = (args) => {
    const byRound = {};
    for (const arg of args) {
      const r = arg.round_number;
      if (!byRound[r]) byRound[r] = [];
      byRound[r].push(arg);
    }
    return byRound;
  };

  const proByRound = groupByRound(grouped.pro);
  const conByRound = groupByRound(grouped.con);
  const allRounds = new Set([
    ...Object.keys(proByRound).map(Number),
    ...Object.keys(conByRound).map(Number),
  ]);
  const sortedRounds = [...allRounds].sort((a, b) => a - b);

  return (
    <div>
      <div className="label-tel mb-2">SIDE BY SIDE</div>
      {sortedRounds.length > 0 ? (
        <div className="space-y-3">
          {sortedRounds.map(round => (
            <div key={round} className="border border-edge">
              <div className="bg-surface px-3 py-1.5 border-b border-edge">
                <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
                  ROUND {round}
                </span>
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 divide-y lg:divide-y-0 lg:divide-x divide-edge">
                <div className="p-2">
                  <div className="label-tel mb-1.5 text-[#10B981]">PRO</div>
                  {proByRound[round]?.length > 0 ? (
                    <div className="space-y-2">
                      {proByRound[round].map(a => (
                        <ArgumentBlock key={a.id} argument={a} />
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-fg-muted italic px-3 py-1">[ none ]</p>
                  )}
                </div>
                <div className="p-2">
                  <div className="label-tel mb-1.5 text-[#EF4444]">CON</div>
                  {conByRound[round]?.length > 0 ? (
                    <div className="space-y-2">
                      {conByRound[round].map(a => (
                        <ArgumentBlock key={a.id} argument={a} />
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-fg-muted italic px-3 py-1">[ none ]</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div>
            <div className="label-tel mb-1.5">PRO</div>
            {grouped.pro.length === 0 ? (
              <p className="text-xs text-fg-muted italic">[ none ]</p>
            ) : (
              <div className="space-y-2">
                {grouped.pro.map((a) => (
                  <ArgumentBlock key={a.id} argument={a} />
                ))}
              </div>
            )}
          </div>
          <div>
            <div className="label-tel mb-1.5">CON</div>
            {grouped.con.length === 0 ? (
              <p className="text-xs text-fg-muted italic">[ none ]</p>
            ) : (
              <div className="space-y-2">
                {grouped.con.map((a) => (
                  <ArgumentBlock key={a.id} argument={a} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {grouped.neutral.length > 0 && (
        <div className="mt-3 border-t border-edge pt-3">
          <div className="label-tel mb-1.5">NEUTRAL / CONTEXT</div>
          <div className="space-y-2">
            {grouped.neutral.map((a) => (
              <ArgumentBlock key={a.id} argument={a} />
            ))}
          </div>
        </div>
      )}
      {grouped.arbiter.length > 0 && (
        <div className="border-t border-edge pt-3 mt-3">
          <div className="label-tel mb-1.5">ARBITER</div>
          <div className="space-y-2">
            {grouped.arbiter.map((a) => (
              <ArgumentBlock key={a.id} argument={a} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DebateRunCard({ run, expanded, onToggle, onExecute, onRerun, onCancel, onRetry, onRerunArbiter, executingId, rerunningId, rerunningArbiterId, viewMode, detailRefreshKey }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!expanded) {
      setDetail(null);
      return;
    }
    if (detail !== null) {
      return;
    }
    setLoading(true);
    getJson(`/work-items/${run.work_item_id}/debates/${run.id}`)
      .then((data) => {
        setDetail(data);
      })
      .catch((err) => {
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }, [expanded, run.id, run.work_item_id, detailRefreshKey]);

  // When detailRefreshKey changes while expanded, clear detail so the effect above refetches
  const prevRefreshKeyRef = useRef(detailRefreshKey);
  useEffect(() => {
    if (expanded && detailRefreshKey !== prevRefreshKeyRef.current) {
      prevRefreshKeyRef.current = detailRefreshKey;
      setDetail(null);
    }
  }, [detailRefreshKey, expanded]);

  const canExecute = run.status === "queued" && !executingId;
  const canRerun = (run.status === "failed" || run.provenance === "execution-bridge-unconfigured" || run.provenance === "execution-disabled") && !rerunningId;

  // Re-run arbiter: only for failed runs with arbiter-specific error and persisted PRO/CON arguments
  const canRerunArbiter = useMemo(() => {
    if (run.status !== "failed") return false;
    if (!run.error_type || !run.error_type.startsWith("arbiter_")) return false;
    if (run.provenance === "execution-disabled" || run.provenance === "execution-bridge-unconfigured") return false;
    if (rerunningArbiterId) return false;
    // Check that we have PRO/CON arguments loaded
    if (!detail?.arguments || detail.arguments.length === 0) return false;
    const hasPro = detail.arguments.some(a => a.side === "pro");
    const hasCon = detail.arguments.some(a => a.side === "con");
    return hasPro && hasCon;
  }, [run.status, run.error_type, run.provenance, rerunningArbiterId, detail?.arguments]);

  const grouped = useMemo(() => {
    if (!detail?.arguments || detail.arguments.length === 0) return null;
    const by = { pro: [], con: [], neutral: [], arbiter: [] };
    for (const arg of detail.arguments) {
      const side = (arg.side || "neutral").toLowerCase();
      (by[side] || by.neutral).push(arg);
    }
    return by;
  }, [detail?.arguments]);

  const hasEdits =
    run.suggested_title || run.suggested_description || run.suggested_acceptance_notes;

  // Determine if this run should show NO DECISION
  const showNoDecision = run.status === "completed" && !run.final_recommendation;

  return (
    <div className="border border-edge bg-surface">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center justify-between gap-3 px-4 py-2.5 border-b border-edge text-left hover:bg-raised transition-colors"
      >
        <div className="flex items-center gap-2 flex-wrap min-w-0">
          <span className="font-mono text-[11px] tracking-telemetry text-fg-muted tabular-nums">
            RUN #{String(run.id).padStart(3, "0")}
          </span>
          <StatusChip kind="status" value={run.status} />
          {run.final_recommendation ? (
            <StatusChip kind="recommendation" value={run.final_recommendation} />
          ) : run.status === "completed" && (
            <span
              className="inline-flex items-center gap-1.5 border px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold"
              style={{
                color: "#F59E0B",
                borderColor: "#F59E0B55",
                backgroundColor: "#F59E0B14",
              }}
            >
              <span className="inline-block h-1.5 w-1.5" style={{ backgroundColor: "#F59E0B" }} />
              NO DECISION
            </span>
          )}
          {run.implementation_readiness && (
            <StatusChip kind="readiness" value={run.implementation_readiness} />
          )}
          <span className="font-mono text-[10px] tracking-telemetry text-fg-secondary">
            {run.trigger?.toUpperCase()} · {run.rounds_requested}R
          </span>
          {run.created_at && (
            <span className="font-mono text-[10px] tracking-telemetry text-fg-muted tabular-nums">
              · {formatTime(run.created_at)}
              {run.completed_at && ` → ${formatTime(run.completed_at)}`}
              {run.generation_duration_ms && ` · ${formatDuration(run.generation_duration_ms)}`}
            </span>
          )}
          {run.model_route && (
            <span className="font-mono text-[10px] tracking-telemetry text-fg-secondary truncate max-w-[200px]" title={run.model_route}>
              · {run.model_route.includes(':') ? run.model_route.substring(run.model_route.indexOf(':') + 1) : run.model_route}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {canExecute && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onExecute(run.id); }}
              className="px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold border border-[#3B82F6] text-[#3B82F6] bg-[#3B82F6]14 hover:bg-[#3B82F6]22 rounded"
              title="Execute this queued debate run"
            >
              ▶ EXECUTE
            </button>
          )}
          {canRerun && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onRerun(run.id); }}
              className="px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold border border-[#F59E0B] text-[#F59E0B] bg-[#F59E0B]14 hover:bg-[#F59E0B]22 rounded"
              title="Rerun this debate with current settings"
            >
              ↻ RERUN
            </button>
          )}
          {canRerunArbiter && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onRerunArbiter(run.id); }}
              className="px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold border border-[#06B6D4] text-[#06B6D4] bg-[#06B6D4]14 hover:bg-[#06B6D4]22 rounded"
              title="Re-run only the Final Arbiter using existing PRO/CON arguments"
            >
              ↻ RE-RUN ARBITER
            </button>
          )}
          {(run.status === "running" || run.status === "warming" || run.status === "generating" || run.worker_status === "claimed") && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onCancel(run.id); }}
              className="px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold border border-red-500 text-red-400 bg-red-500/14 hover:bg-red-500/22 rounded"
              title="Cancel this debate run"
            >
              ⏹ CANCEL
            </button>
          )}
          {run.status === "failed" && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onRetry(run.id); }}
              className="px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold border border-[#10B981] text-[#10B981] bg-[#10B981]14 hover:bg-[#10B981]22 rounded"
              title="Retry this failed debate run"
            >
              ↻ RETRY
            </button>
          )}
          <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
            {expanded ? "[ − ]" : "[ + ]"}
          </span>
        </div>
      </button>

      {expanded && (
        <div className="px-4 py-3 space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <div>
              <div className="label-tel">CREATED</div>
              <div className="font-mono text-xs mt-0.5 tabular-nums">
                {formatTime(run.created_at) || "—"}
              </div>
            </div>
            <div>
              <div className="label-tel">COMPLETED</div>
              <div className="font-mono text-xs mt-0.5 tabular-nums">
                {formatTime(run.completed_at) || "—"}
              </div>
            </div>
            <div>
              <div className="label-tel">PROVENANCE</div>
              <div className="font-mono text-xs mt-0.5 break-words">
                {run.provenance || run.model_route || "—"}
              </div>
            </div>
          </div>

          {run.error_message && (
            <ErrorBanner message={run.error_message} />
          )}

          <div className="border-l-2 border-fg-muted/40 pl-3 py-1 text-[11px] font-mono uppercase tracking-telemetry text-fg-muted">
            ADVISORY ONLY · OPERATOR MUST APPROVE MANUALLY
          </div>

          {run.summary && (
            <div>
              <div className="label-tel mb-1">ARBITER SUMMARY</div>
              <p className="text-sm text-fg-primary whitespace-pre-wrap leading-relaxed">
                {run.summary}
              </p>
            </div>
          )}
          {run.risks && (
            <div>
              <div className="label-tel mb-1">TOP RISKS</div>
              <p className="text-sm text-fg-primary whitespace-pre-wrap leading-relaxed">
                {run.risks}
              </p>
            </div>
          )}

          {hasEdits && (
            <div className="border border-edge bg-canvas px-3 py-2 space-y-1.5">
              <div className="label-tel-strong">SUGGESTED EDITS</div>
              {run.suggested_title && (
                <div>
                  <div className="label-tel">TITLE</div>
                  <p className="text-sm text-fg-primary">{run.suggested_title}</p>
                </div>
              )}
              {run.suggested_description && (
                <div>
                  <div className="label-tel">DESCRIPTION</div>
                  <p className="text-sm text-fg-primary whitespace-pre-wrap">
                    {run.suggested_description}
                  </p>
                </div>
              )}
              {run.suggested_acceptance_notes && (
                <div>
                  <div className="label-tel">ACCEPTANCE NOTES</div>
                  <p className="text-sm text-fg-primary whitespace-pre-wrap">
                    {run.suggested_acceptance_notes}
                  </p>
                </div>
              )}
            </div>
          )}

          {error && <ErrorBanner message={error} />}
          {loading && <SkeletonBlock rows={3} />}

          {/* Final Decision Section - always show for completed runs */}
          {(run.status === "completed" || run.status === "failed") && (
            <div className="border border-edge bg-canvas px-3 py-2 mt-3">
              <div className="label-tel-strong mb-2">FINAL DECISION</div>
              {run.final_recommendation ? (
                <div className="space-y-1.5">
                  <div className="flex items-center gap-2 flex-wrap">
                    <StatusChip kind="recommendation" value={run.final_recommendation} />
                    {run.implementation_readiness && (
                      <StatusChip kind="readiness" value={run.implementation_readiness} />
                    )}
                  </div>
                  {run.summary && (
                    <div>
                      <div className="label-tel">RATIONALE</div>
                      <p className="text-sm text-fg-primary whitespace-pre-wrap">{run.summary}</p>
                    </div>
                  )}
                </div>
              ) : run.error_message ? (
                <div className="text-sm text-[#F59E0B]">
                  <strong>NO DECISION</strong> — {run.error_message}
                </div>
              ) : (
                <div className="text-sm text-fg-muted">
                  <strong>NO DECISION</strong> — Arbiter output not available
                </div>
              )}
            </div>
          )}

          {grouped && (
            <div className="space-y-4">
              {viewMode === "chronological" ? (
                <div>
                  <div className="label-tel mb-1.5">CHRONOLOGICAL FLOW</div>
                  <div className="space-y-2">
                    {detail.arguments
                      .sort((a, b) => {
                        if (a.round_number !== b.round_number) return a.round_number - b.round_number;
                        return a.id - b.id;
                      })
                      .map((a) => (
                        <ArgumentBlock key={a.id} argument={a} />
                      ))}
                  </div>
                </div>
              ) : (
                <SideBySideView grouped={grouped} />
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConfirmModal({ open, title, message, confirmLabel, cancelLabel, onConfirm, onCancel, danger }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface border border-edge-strong shadow-2xl max-w-md w-full mx-4">
        <div className="px-4 py-3 border-b border-edge">
          <h3 className="font-mono text-sm tracking-telemetry font-semibold text-fg-primary">
            {title}
          </h3>
        </div>
        <div className="px-4 py-3">
          <p className="text-sm text-fg-secondary whitespace-pre-wrap">{message}</p>
        </div>
        <div className="px-4 py-3 border-t border-edge flex items-center justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold border border-edge text-fg-secondary hover:text-fg-primary hover:border-fg-secondary rounded"
          >
            {cancelLabel || "CANCEL"}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={`px-3 py-1.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold border rounded ${
              danger
                ? "border-red-500 text-red-400 bg-red-500/14 hover:bg-red-500/22"
                : "border-[#F59E0B] text-[#F59E0B] bg-[#F59E0B]14 hover:bg-[#F59E0B]22"
            }`}
          >
            {confirmLabel || "CONFIRM"}
          </button>
        </div>
      </div>
    </div>
  );
}

export default function DebatePanel({ workItemId }) {
  const [runs, setRuns] = useState([]);
  const [inputs, setInputs] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [rounds, setRounds] = useState(2);
  const [opContent, setOpContent] = useState("");
  const [opStance, setOpStance] = useState("auto_assign");
  const [busy, setBusy] = useState(null);
  const [expandedId, setExpandedId] = useState(null);
  const [debateRefreshKey, setDebateRefreshKey] = useState(0);
  const [executingId, setExecutingId] = useState(null);
  const [rerunningId, setRerunningId] = useState(null);
  const [rerunningArbiterId, setRerunningArbiterId] = useState(null);
  const [showOlderFailed, setShowOlderFailed] = useState(false);
  const [viewMode, setViewMode] = useState("chronological");
  const [showResetConfirm, setShowResetConfirm] = useState(false);
  const [resetting, setResetting] = useState(false);
  const pollingIntervalRef = useRef(null);
  const expandedIdRef = useRef(null);
  const VISIBLE_FAILED_LIMIT = 2;

  useEffect(() => {
    expandedIdRef.current = expandedId;
  }, [expandedId]);

  function refresh() {
    setError(null);
    Promise.all([
      getJson(`/work-items/${workItemId}/debates?view=active`),
      getJson(`/work-items/${workItemId}/debate-inputs`),
    ])
      .then(([rs, is]) => {
        setRuns(rs);
        setInputs(is);
        if (rs.length > 0 && expandedIdRef.current === null && !window.debatePanelUserCollapsed) {
          setExpandedId(rs[0].id);
        }
        const hasActiveExecution = rs.some(r => 
          r.status === "running" || 
          r.status === "warming" || 
          r.status === "generating" ||
          r.execution_stage === "warming" ||
          r.execution_stage === "generating" ||
          r.execution_stage === "running"
        );
        if (hasActiveExecution && !pollingIntervalRef.current) {
          const interval = setInterval(() => {
            refresh();
          }, 2000);
          pollingIntervalRef.current = interval;
        } else if (!hasActiveExecution && pollingIntervalRef.current) {
          clearInterval(pollingIntervalRef.current);
          pollingIntervalRef.current = null;
          setExecutingId(null);
          setRerunningId(null);
        }
      })
      .catch((err) => {
        console.error('[DebatePanel] Refresh error:', err);
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    refresh();
  }, [workItemId]);

  useEffect(() => {
    return () => {
      if (pollingIntervalRef.current) {
        clearInterval(pollingIntervalRef.current);
        pollingIntervalRef.current = null;
      }
    };
  }, []);

  function runDebate() {
    const clamped = Math.max(
      DEBATE_MIN_ROUNDS,
      Math.min(DEBATE_MAX_ROUNDS, Number(rounds) || 2),
    );
    setBusy("run");
    postJson(`/work-items/${workItemId}/debates`, {
      rounds: clamped,
      trigger: "manual_rerun",
    })
      .then((created) => {
        setExpandedId(created.id);
        return postJson(`/work-items/${workItemId}/debates/${created.id}/execute`, {});
      })
      .then(() => {
        setExecutingId(true);
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setBusy(null));
  }

  function executeRun(runId) {
    setExecutingId(runId);
    postJson(`/work-items/${workItemId}/debates/${runId}/execute`, {})
      .then(() => {
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setExecutingId(null));
  }

  function cancelRun(runId) {
    if (!confirm("Cancel this debate run? The worker will stop at the next safe point.")) return;
    postJson(`/work-items/${workItemId}/debates/${runId}/cancel`, {})
      .then(() => {
        refresh();
      })
      .catch((err) => setError(err.message));
  }

  function retryRun(runId) {
    postJson(`/work-items/${workItemId}/debates/${runId}/retry`, {})
      .then((created) => {
        setExpandedId(created.id);
        return postJson(`/work-items/${workItemId}/debates/${created.id}/execute`, {});
      })
      .then(() => {
        setExecutingId(true);
        refresh();
      })
      .catch((err) => setError(err.message));
  }

  function rerunRun(runId) {
    setRerunningId(runId);
    const originalRun = runs.find(r => r.id === runId);
    const roundsToUse = originalRun?.rounds_requested || 2;
    
    postJson(`/work-items/${workItemId}/debates`, {
      rounds: roundsToUse,
      trigger: "manual_rerun",
    })
      .then((created) => {
        setExpandedId(created.id);
        return postJson(`/work-items/${workItemId}/debates/${created.id}/execute`, {});
      })
      .then(() => {
        setExecutingId(true);
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setRerunningId(null));
  }

  function rerunArbiterRun(runId) {
    if (!confirm("Re-run only the Final Arbiter for this debate? Existing PRO/CON arguments will be reused.")) return;
    setRerunningArbiterId(runId);
    postJson(`/work-items/${workItemId}/debates/${runId}/rerun-arbiter`, {})
      .then((updated) => {
        // Force expanded card detail to refetch by bumping refresh key
        setDebateRefreshKey((k) => k + 1);
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setRerunningArbiterId(null));
  }

  function submitOperatorInput() {
    if (!opContent.trim()) return;
    setBusy("op");
    postJson(`/work-items/${workItemId}/debate-inputs`, {
      content: opContent.trim(),
      stance_requested: opStance,
    })
      .then(() => {
        setOpContent("");
        setOpStance("auto_assign");
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setBusy(null));
  }

  function resetDebates() {
    setResetting(true);
    postJson(`/work-items/${workItemId}/debates/reset`, {
      reason: "Operator reset debate history",
      mode: "archive",
    })
      .then(() => {
        setShowResetConfirm(false);
        setExpandedId(null);
        window.debatePanelUserCollapsed = true;
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setResetting(false));
  }

  function renderRuns() {
    if (runs.length === 0) {
      return (
        <EmptyState
          title="NO DEBATE RUNS YET"
          hint="A run will queue automatically when this work item is in a debate-eligible status, or you can trigger one above."
          glyph="[ ∅ ]"
        />
      );
    }

    const activeRuns = runs.filter(r => r.status !== 'failed');
    const failedRuns = runs.filter(r => r.status === 'failed');
    
    const visibleFailed = failedRuns.slice(0, VISIBLE_FAILED_LIMIT);
    const collapsedFailed = failedRuns.slice(VISIBLE_FAILED_LIMIT);
    
    const displayRuns = [...activeRuns, ...visibleFailed];
    const hasCollapsed = collapsedFailed.length > 0;
    
    return (
      <div>
        <div className="space-y-2">
          {displayRuns.map((run) => (
            <DebateRunCard
              key={run.id}
              run={run}
              expanded={expandedId === run.id}
              onToggle={() => {
                const newExpandedId = expandedId === run.id ? null : run.id;
                setExpandedId(newExpandedId);
                window.debatePanelUserCollapsed = (newExpandedId === null);
              }}
              onExecute={executeRun}
              onRerun={rerunRun}
              onCancel={cancelRun}
              onRetry={retryRun}
              onRerunArbiter={rerunArbiterRun}
              executingId={executingId === true || executingId === run.id}
              rerunningId={rerunningId === true || rerunningId === run.id}
              rerunningArbiterId={rerunningArbiterId === true || rerunningArbiterId === run.id}
              viewMode={viewMode}
              detailRefreshKey={debateRefreshKey}
            />
          ))}
        </div>
        
        {hasCollapsed && (
          <div className="border border-edge bg-canvas rounded p-3">
            <button
              type="button"
              onClick={() => setShowOlderFailed(!showOlderFailed)}
              className="font-mono text-xs text-fg-secondary hover:text-fg-primary flex items-center gap-2"
            >
              {showOlderFailed ? "▲" : "▼"}
              {collapsedFailed.length} older failed attempt{collapsedFailed.length > 1 ? 's' : ''} — {showOlderFailed ? "Hide" : "Show"}
            </button>
            
            {showOlderFailed && (
              <div className="mt-2 space-y-2">
                {collapsedFailed.map((run) => (
                  <DebateRunCard
                    key={run.id}
                    run={run}
                    expanded={expandedId === run.id}
                    onToggle={() => setExpandedId(expandedId === run.id ? null : run.id)}
                    onExecute={executeRun}
                    onRerun={rerunRun}
                    onCancel={cancelRun}
                    onRetry={retryRun}
                    onRerunArbiter={rerunArbiterRun}
                    executingId={executingId === true || executingId === run.id}
                    rerunningId={rerunningId === true || rerunningId === run.id}
                    rerunningArbiterId={rerunningArbiterId === true || rerunningArbiterId === run.id}
                    viewMode={viewMode}
                    detailRefreshKey={debateRefreshKey}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ConfirmModal
        open={showResetConfirm}
        title="RESET DEBATE HISTORY"
        message={"This will archive all active debate runs for this work item.\n\nArchived runs are hidden from the active view but can be accessed by operators.\n\nThis action cannot be undone for hard-deleted runs.\n\nProceed with archive (soft-hide) mode?"}
        confirmLabel={resetting ? "ARCHIVING…" : "ARCHIVE RUNS"}
        cancelLabel="CANCEL"
        onConfirm={resetDebates}
        onCancel={() => setShowResetConfirm(false)}
        danger={false}
      />

      <Panel
        title="DEBATE"
        subtitle="// advisory · operator must approve manually"
        right={
          <div className="flex items-center gap-2">
            <label className="label-tel">ROUNDS:</label>
            <input
              type="number"
              min={DEBATE_MIN_ROUNDS}
              max={DEBATE_MAX_ROUNDS}
              value={rounds}
              onChange={(e) => setRounds(e.target.value)}
              className="w-14 bg-canvas border border-edge-strong px-2 py-1 font-mono text-xs tabular-nums outline-none focus:border-fg-primary"
            />
            <Button
              variant="primary"
              onClick={runDebate}
              disabled={busy === "run"}
            >
              {busy === "run" ? "QUEUING & EXECUTING…" : "[▶] RUN DEBATE"}
            </Button>
          </div>
        }
      >
        <ErrorBanner message={error} />
        <p className="text-xs text-fg-secondary">
          Rounds clamped to {DEBATE_MIN_ROUNDS}–{DEBATE_MAX_ROUNDS}. Each run
          produces a recommendation: APPROVE AS IS, APPROVE WITH EDITS,
          SPLIT FIRST, NEEDS MORE DETAIL, or DO NOT BUILD NOW. The operator
          retains final say.
        </p>
      </Panel>

      <Panel
        title="DEBATE RUNS"
        subtitle={`// ${runs.length} run${runs.length === 1 ? "" : "s"}`}
        right={
          <div className="flex items-center gap-2">
            {/* View mode toggle */}
            <div className="flex gap-0.5 border border-edge rounded overflow-hidden">
              {VIEW_MODES.map((mode) => (
                <button
                  key={mode.value}
                  type="button"
                  onClick={() => setViewMode(mode.value)}
                  className={`px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold ${
                    viewMode === mode.value
                      ? "bg-raised text-fg-primary border-fg-primary"
                      : "text-fg-muted hover:text-fg-secondary"
                  }`}
                  title={`Switch to ${mode.label.toLowerCase()} view`}
                >
                  {mode.value === "chronological" ? "◫ CHRON" : "◧ SIDE×SIDE"}
                </button>
              ))}
            </div>
            {/* Clear/Reset Debates button */}
            {runs.length > 0 && (
              <button
                type="button"
                onClick={() => setShowResetConfirm(true)}
                disabled={busy === "run"}
                className="px-2 py-0.5 text-[10px] font-mono uppercase tracking-telemetry font-semibold border border-[#F59E0B] text-[#F59E0B] bg-[#F59E0B]14 hover:bg-[#F59E0B]22 rounded"
                title="Archive all debate runs (soft-hide, reversible)"
              >
                ⟲ RESET DEBATES
              </button>
            )}
          </div>
        }
      >
        {loading ? (
          <SkeletonBlock rows={3} />
        ) : (
          renderRuns()
        )}
      </Panel>

      <Panel
        title="OPERATOR ARGUMENTS"
        subtitle={`// ${inputs.length} record${inputs.length === 1 ? "" : "s"}`}
      >
        <div className="space-y-2 mb-3">
          <textarea
            value={opContent}
            onChange={(e) => setOpContent(e.target.value)}
            rows={2}
            placeholder="// argument or counter-argument the debate should consider"
            className="w-full px-3 py-2 text-sm font-mono bg-canvas border border-edge-strong focus:border-fg-primary outline-none"
          />
          <div className="flex items-center gap-2 flex-wrap">
            <label className="label-tel">STANCE:</label>
            <div className="flex gap-1.5">
              {STANCE_OPTIONS.map((opt) => (
                <button
                  type="button"
                  key={opt.value}
                  onClick={() => setOpStance(opt.value)}
                  className={`px-2 py-1 font-mono uppercase tracking-telemetry text-[10px] font-semibold border ${
                    opStance === opt.value
                      ? "border-fg-primary text-fg-primary bg-raised"
                      : "border-edge text-fg-secondary hover:text-fg-primary"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <div className="flex-1" />
            <Button
              variant="ghost"
              onClick={submitOperatorInput}
              disabled={busy === "op" || !opContent.trim()}
            >
              {busy === "op" ? "ADDING…" : "+ ADD ARGUMENT"}
            </Button>
          </div>
        </div>
        {inputs.length === 0 ? (
          <EmptyState
            title="NO OPERATOR ARGUMENTS"
            hint="Add a pro/con/neutral argument the debate should weigh."
            glyph="[ ∅ ]"
          />
        ) : (
          <ul className="divide-y divide-edge/60">
            {inputs.map((inp) => (
              <li
                key={inp.id}
                className="grid grid-cols-[110px_1fr_120px] gap-3 py-2 items-start"
              >
                <StatusChip
                  kind="side"
                  value={inp.stance_assigned || inp.stance_requested}
                />
                <p className="text-sm text-fg-primary whitespace-pre-wrap">
                  {inp.content}
                </p>
                <span className="font-mono text-[10px] tracking-telemetry text-fg-muted text-right">
                  {inp.considered_in_run_id
                    ? `IN RUN #${String(inp.considered_in_run_id).padStart(3, "0")}`
                    : "PENDING"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
