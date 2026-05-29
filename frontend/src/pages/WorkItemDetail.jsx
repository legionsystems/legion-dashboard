import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJson, postJson } from "../api/client.js";
import { StatusBadge, TypeBadge } from "../components/badges.jsx";
import { Panel, FieldRow } from "../components/panel.jsx";
import { Button } from "../components/buttons.jsx";
import {
  ErrorBanner,
  SkeletonBlock,
  EmptyState,
} from "../components/states.jsx";

function formatTime(iso) {
  if (!iso) return null;
  return new Date(iso).toISOString().replace("T", " ").split(".")[0] + "Z";
}

export default function WorkItemDetail() {
  const { id } = useParams();
  const [item, setItem] = useState(null);
  const [followUps, setFollowUps] = useState([]);
  const [error, setError] = useState(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(null);

  function refresh() {
    setError(null);
    getJson(`/work-items/${id}`)
      .then(setItem)
      .catch((err) => setError(err.message));
    getJson(`/work-items/${id}/follow-ups`)
      .then(setFollowUps)
      .catch(() => undefined);
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function approve() {
    setBusy("approve");
    postJson(`/work-items/${id}/approve`)
      .then(() => refresh())
      .catch((err) => setError(err.message))
      .finally(() => setBusy(null));
  }

  function block() {
    setBusy("block");
    postJson(`/work-items/${id}/block`, { override_reason: reason })
      .then(() => {
        setReason("");
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setBusy(null));
  }

  if (error && !item) {
    return (
      <div className="space-y-4">
        <ErrorBanner message={error} />
        <Link
          to="/work-items"
          className="font-mono uppercase tracking-telemetry text-xs text-fg-secondary hover:text-fg-primary"
        >
          ← BACK TO REGISTRY
        </Link>
      </div>
    );
  }

  if (!item) {
    return (
      <div className="space-y-4">
        <div className="skeleton h-10 w-2/3" />
        <Panel title="LOADING">
          <SkeletonBlock rows={4} />
        </Panel>
      </div>
    );
  }

  const isBlocked = item.status === "blocked";

  return (
    <div className="space-y-5">
      <div>
        <Link
          to="/work-items"
          className="font-mono uppercase tracking-telemetry text-[11px] text-fg-secondary hover:text-fg-primary"
        >
          ← BACK TO REGISTRY
        </Link>
      </div>

      <ErrorBanner message={error} />

      <div className="border border-edge bg-surface">
        <div className="border-b border-edge px-5 py-4">
          <div className="flex flex-col md:flex-row md:items-start md:justify-between gap-4">
            <div className="min-w-0">
              <div className="flex items-center gap-3 mb-2">
                <span className="font-mono text-[11px] tracking-telemetry text-fg-muted">
                  WORK ITEM
                </span>
                <span className="font-mono text-[11px] tracking-telemetry text-fg-secondary tabular-nums">
                  #{String(item.id).padStart(4, "0")}
                </span>
                <TypeBadge type={item.type} />
                <StatusBadge status={item.status} />
                {item.approved_by_operator && (
                  <span className="font-mono text-[10px] tracking-telemetry text-st-completed border border-st-completed/60 bg-st-completed/10 px-1.5 py-0.5">
                    ✓ OPERATOR APPROVED
                  </span>
                )}
              </div>
              <h1 className="font-display font-extrabold tracking-tighter-display text-fg-primary text-2xl md:text-3xl leading-tight break-words">
                {item.title}
              </h1>
            </div>
            <div className="flex gap-2 shrink-0">
              <Link to={`/work-items/${item.id}/edit`}>
                <Button variant="ghost">EDIT</Button>
              </Link>
              <Button
                variant="success"
                disabled={item.approved_by_operator || busy === "approve"}
                onClick={approve}
              >
                {item.approved_by_operator
                  ? "✓ APPROVED"
                  : busy === "approve"
                  ? "APPROVING…"
                  : "APPROVE"}
              </Button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-px bg-edge">
          <div className="bg-surface px-5 py-3">
            <div className="label-tel">CREATED</div>
            <div className="font-mono text-sm mt-1 tabular-nums">
              {formatTime(item.created_at) || "—"}
            </div>
          </div>
          <div className="bg-surface px-5 py-3">
            <div className="label-tel">LAST UPDATED</div>
            <div className="font-mono text-sm mt-1 tabular-nums">
              {formatTime(item.updated_at) || "—"}
            </div>
          </div>
          <div className="bg-surface px-5 py-3">
            <div className="label-tel">APPROVAL TIMESTAMP</div>
            <div className="font-mono text-sm mt-1 tabular-nums">
              {formatTime(item.approval_timestamp) || "—"}
            </div>
          </div>
        </div>
      </div>

      <Panel title="BODY" subtitle="// narrative payload">
        {item.body ? (
          <p className="whitespace-pre-wrap text-sm text-fg-primary leading-relaxed">
            {item.body}
          </p>
        ) : (
          <p className="text-sm text-fg-muted italic">[ no body provided ]</p>
        )}
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Panel title="PROVENANCE" subtitle="// builder & reviewer">
          <FieldRow label="BUILDER PROFILE" value={item.builder_profile} mono />
          <FieldRow label="BUILDER MODEL" value={item.builder_model} mono />
          <FieldRow
            label="BUILDER PROVIDER"
            value={item.builder_provider}
            mono
          />
          <FieldRow
            label="REVIEWER PROFILE"
            value={item.reviewer_profile}
            mono
          />
          <FieldRow label="REVIEWER MODEL" value={item.reviewer_model} mono />
          <FieldRow
            label="REVIEWER PROVIDER"
            value={item.reviewer_provider}
            mono
          />
          <FieldRow
            label="SAME-MODEL BLOCKED"
            value={
              item.same_model_blocked ? (
                <span className="text-alert">YES</span>
              ) : (
                "NO"
              )
            }
            mono
          />
        </Panel>

        <Panel title="DELIVERY" subtitle="// PR & merge">
          <FieldRow label="PR URL" value={item.pr_url} mono />
          <FieldRow label="MERGE COMMIT" value={item.merge_commit_sha} mono />
          <FieldRow
            label="OVERRIDE REASON"
            value={item.override_reason}
            mono
          />
          <FieldRow
            label="OVERRIDE STAMP"
            value={formatTime(item.override_timestamp)}
            mono
          />
        </Panel>
      </div>

      <Panel
        title="BLOCK / OVERRIDE"
        subtitle="// operator stop"
      >
        <p className="text-xs text-fg-secondary mb-2">
          Blocks this work item and records an override reason. Stamped with
          the current operator timestamp.
        </p>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          placeholder="// describe why this work item must be halted"
          className="w-full px-3 py-2 text-sm font-mono bg-canvas border border-edge-strong focus:border-alert outline-none"
        />
        <div className="mt-2 flex items-center justify-between gap-3 flex-wrap">
          <span className="label-tel">
            {isBlocked ? "STATUS: ALREADY BLOCKED" : "READY"}
          </span>
          <Button
            variant="danger"
            onClick={block}
            disabled={busy === "block"}
          >
            {busy === "block" ? "BLOCKING…" : "[!] BLOCK WORK ITEM"}
          </Button>
        </div>
      </Panel>

      <Panel
        title="FOLLOW-UPS"
        subtitle={`// ${followUps.length} record${followUps.length === 1 ? "" : "s"}`}
      >
        {followUps.length === 0 ? (
          <EmptyState
            title="NO FOLLOW-UPS"
            hint="Reviewer findings and post-delivery notes will appear here."
            glyph="[ ∅ ]"
          />
        ) : (
          <ul className="divide-y divide-edge/60">
            {followUps.map((fu) => (
              <li
                key={fu.id}
                className="grid grid-cols-[110px_1fr_110px] gap-3 py-3 items-start"
              >
                <span
                  className={`font-mono text-[11px] uppercase tracking-telemetry font-semibold px-2 py-0.5 border ${
                    fu.severity === "blocking"
                      ? "border-alert/60 text-alert bg-alert/10"
                      : "border-edge text-fg-secondary bg-canvas"
                  } w-fit`}
                >
                  {fu.severity || "—"}
                </span>
                <div className="min-w-0">
                  <div className="text-sm text-fg-primary font-semibold">
                    {fu.title || "(untitled)"}
                  </div>
                  {fu.body && (
                    <p className="text-sm text-fg-secondary mt-1 whitespace-pre-wrap">
                      {fu.body}
                    </p>
                  )}
                </div>
                <span className="font-mono text-[10px] tracking-telemetry text-fg-muted text-right">
                  {fu.status?.toUpperCase()}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
