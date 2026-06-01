import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJson, postJson, deleteRequest, getAttachmentDownloadUrl } from "../api/client.js";
import { StatusBadge, TypeBadge } from "../components/badges.jsx";
import { Panel, FieldRow } from "../components/panel.jsx";
import { Button, OperatorButton } from "../components/buttons.jsx";
import {
  ErrorBanner,
  SkeletonBlock,
  EmptyState,
} from "../components/states.jsx";
import DebatePanel from "../components/DebatePanel.jsx";

function formatTime(iso) {
  if (!iso) return null;
  const date = new Date(iso);
  return date.toLocaleString('en-AU', {
    timeZone: 'Australia/Sydney',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).replace(',', '');
}

export default function WorkItemDetail() {
  const { id } = useParams();
  const [item, setItem] = useState(null);
  const [followUps, setFollowUps] = useState([]);
  const [error, setError] = useState(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(null);
  const [showArchiveInput, setShowArchiveInput] = useState(false);
  const [archiveReason, setArchiveReason] = useState("");
  const [builderTask, setBuilderTask] = useState(null);
  const [builderBusy, setBuilderBusy] = useState(false);
  const [attachments, setAttachments] = useState([]);

  function refresh() {
    setError(null);
    getJson(`/work-items/${id}`)
      .then(setItem)
      .catch((err) => setError(err.message));
    getJson(`/work-items/${id}/follow-ups`)
      .then(setFollowUps)
      .catch(() => undefined);
    getJson(`/builder/work-items/${id}/builder`)
      .then(setBuilderTask)
      .catch(() => undefined);
    getJson(`/work-items/${id}/attachments`)
      .then(setAttachments)
      .catch(() => undefined);
  }

  useEffect(() => {
    refresh();
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

  function archive() {
    setBusy("archive");
    postJson(`/work-items/${id}/archive`, { reason: archiveReason || undefined })
      .then(() => {
        setArchiveReason("");
        setShowArchiveInput(false);
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setBusy(null));
  }

  function restore() {
    setBusy("restore");
    postJson(`/work-items/${id}/restore`, {})
      .then(() => refresh())
      .catch((err) => setError(err.message))
      .finally(() => setBusy(null));
  }

  function startBuild() {
    setBuilderBusy(true);
    postJson(`/builder/work-items/${id}/start-build`, {})
      .then((data) => {
        setBuilderTask(data);
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setBuilderBusy(false));
  }

  function syncBuilder() {
    if (!builderTask) return;
    setBuilderBusy(true);
    postJson(`/builder/tasks/${builderTask.id}/sync`, {})
      .then((data) => setBuilderTask(data))
      .catch((err) => setError(err.message))
      .finally(() => setBuilderBusy(false));
  }

  const canStartBuild = item?.approved_by_operator && !builderTask;
  const blockedTypes = new Set(["note"]);
  const canStartBuildType = !blockedTypes.has(item?.type?.toLowerCase());

  if (error && !item) {
    return (
      <div className="space-y-4">
        <ErrorBanner message={error} />
        <Link
          to="/work-items"
          className="font-mono uppercase tracking-telemetry text-xs text-fg-secondary hover:text-fg-primary"
        >
          ← BACK TO WORK ITEMS
        </Link>
      </div>
    );
  }

  if (!item) {
    return <SkeletonBlock lines={8} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Link
              to="/work-items"
              className="font-mono uppercase tracking-telemetry text-xs text-fg-secondary hover:text-fg-primary"
            >
              ← BACK
            </Link>
            <TypeBadge type={item.type} />
            <StatusBadge status={item.status} />
            {item.approved_by_operator && (
              <span className="text-xs text-st-completed font-mono">✓ APPROVED</span>
            )}
          </div>
          <h1 className="text-xl font-semibold text-fg-primary">{item.title}</h1>
        </div>
        <div className="flex items-center gap-2">
          <Link to={`/work-items/${id}/edit`}>
            <Button variant="ghost">EDIT</Button>
          </Link>
          {!item.archived ? (
            <>
              {!showArchiveInput ? (
                <Button
                  variant="danger"
                  onClick={() => setShowArchiveInput(true)}
                >
                  ARCHIVE
                </Button>
              ) : (
                <div className="flex gap-2 items-center">
                  <input
                    type="text"
                    value={archiveReason}
                    onChange={(e) => setArchiveReason(e.target.value)}
                    placeholder="Archive reason (optional)"
                    className="px-2 py-1 text-sm bg-canvas border border-edge outline-none"
                  />
                  <Button
                    variant="danger"
                    onClick={archive}
                    disabled={busy === "archive"}
                  >
                    {busy === "archive" ? "ARCHIVING…" : "CONFIRM"}
                  </Button>
                  <Button
                    variant="ghost"
                    onClick={() => {
                      setShowArchiveInput(false);
                      setArchiveReason("");
                    }}
                  >
                    CANCEL
                  </Button>
                </div>
              )}
            </>
          ) : (
            <Button
              variant="success"
              onClick={restore}
              disabled={busy === "restore"}
            >
              {busy === "restore" ? "RESTORING…" : "RESTORE"}
            </Button>
          )}
          {canStartBuild && canStartBuildType && (
            <OperatorButton
              onClick={startBuild}
              disabled={builderBusy}
              title={!canStartBuildType ? "Note type cannot start build" : "Create Hermes Kanban task and start build"}
            >
              {builderBusy ? "STARTING BUILD…" : "START BUILD"}
            </OperatorButton>
          )}
          {!canStartBuildType && (
            <span className="text-xs text-fg-muted" title="Note type cannot start build">
              BUILD N/A
            </span>
          )}
          {!item.approved_by_operator && (
            <Button
              variant="success"
              disabled={busy === "approve"}
              onClick={approve}
            >
              {busy === "approve" ? "APPROVING…" : "APPROVE"}
            </Button>
          )}
        </div>
      </div>

      {builderTask && (
        <Panel title="BUILDER" subtitle="// Hermes Kanban bridge">
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <FieldRow label="HERMES TASK" value={builderTask.hermes_task_id} mono />
              <FieldRow label="BOARD" value={builderTask.hermes_board} mono />
              <FieldRow label="STATUS" value={builderTask.hermes_status?.toUpperCase()} mono />
              <FieldRow label="ASSIGNEE" value={builderTask.hermes_assignee || "—"} mono />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <FieldRow label="CREATED" value={formatTime(builderTask.created_at)} mono />
              <FieldRow label="LAST SYNC" value={formatTime(builderTask.last_sync_at)} mono />
              <FieldRow label="HERMES STATUS" value={builderTask.last_known_hermes_status?.toUpperCase() || "—"} mono />
              <FieldRow label="PR URL" value={builderTask.pr_url || "—"} mono />
            </div>
            {builderTask.branch_name && (
              <div className="grid grid-cols-2 gap-3">
                <FieldRow label="BRANCH" value={builderTask.branch_name} mono />
                <FieldRow label="MERGE SHA" value={builderTask.merge_commit_sha || "—"} mono />
              </div>
            )}
            {builderTask.hermes_result && (
              <div className="text-xs text-fg-muted border border-edge bg-canvas/50 p-3">
                <div className="label-tel mb-1">RESULT</div>
                <p className="whitespace-pre-wrap">{builderTask.hermes_result}</p>
              </div>
            )}
            <div className="flex gap-2 items-center">
              <Button variant="ghost" onClick={syncBuilder} disabled={builderBusy}>
                {builderBusy ? "SYNCING…" : "SYNC"}
              </Button>
              {builderTask.generated_prompt_snapshot && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-fg-secondary hover:text-fg-primary font-mono uppercase tracking-telemetry text-xs">VIEW PROMPT</summary>
                  <pre className="mt-2 p-3 bg-canvas text-fg-primary text-xs overflow-auto max-h-96 whitespace-pre-wrap font-mono border border-edge">
                    {builderTask.generated_prompt_snapshot}
                  </pre>
                </details>
              )}
            </div>
            <p className="text-xs text-fg-muted italic">
              Warning: Ready + assigned tasks may auto-start via Hermes orchestration.
            </p>
          </div>
        </Panel>
      )}

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

      <Panel title="BODY" subtitle="// narrative payload">
        {item.body ? (
          <p className="whitespace-pre-wrap text-sm text-fg-primary leading-relaxed">
            {item.body}
          </p>
        ) : (
          <p className="text-sm text-fg-muted italic">[ no body provided ]</p>
        )}
      </Panel>

      <Panel title="ATTACHMENTS" subtitle={`// ${attachments.length} file(s)`}>
        {attachments.length === 0 ? (
          <p className="text-sm text-fg-muted italic">[ no attachments ]</p>
        ) : (
          <div className="space-y-2">
            {attachments.map((att) => {
              const isImage = att.content_type.startsWith("image/");
              const downloadUrl = getAttachmentDownloadUrl(id, att.id);
              return (
                <div
                  key={att.id}
                  className="flex items-center justify-between px-3 py-2 bg-canvas border border-edge"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-fg-secondary text-sm">
                      {isImage ? "🖼️" : "📎"}
                    </span>
                    <a
                      href={downloadUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm font-mono text-fg-primary hover:text-accent truncate"
                    >
                      {att.original_filename}
                    </a>
                    <span className="text-xs text-fg-muted font-mono">
                      ({att.content_type})
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className="text-xs text-fg-muted font-mono tabular-nums">
                      {att.file_size > 1024 * 1024
                        ? `${(att.file_size / (1024 * 1024)).toFixed(1)} MB`
                        : `${Math.round(att.file_size / 1024)} KB`}
                    </span>
                    <a
                      href={downloadUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-mono uppercase tracking-telemetry text-fg-secondary hover:text-fg-primary"
                    >
                      DOWNLOAD
                    </a>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <DebatePanel workItemId={item.id} />
    </div>
  );
}
