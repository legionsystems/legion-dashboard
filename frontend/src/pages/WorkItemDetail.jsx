import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  getJson,
  postJson,
  deleteRequest,
  getAttachmentDownloadUrl,
  certifyWorkItem,
  rejectWorkItem,
  rejectWorkItemWithChanges,
  getActiveRepoLocks,
  releaseRepoLock,
  deployPreview,
  revertPreview,
  mergeWorkItem,
  completeWorkItem,
  ApiError,
} from "../api/client.js";
import { StatusBadge, TypeBadge } from "../components/badges.jsx";
import { Panel, FieldRow } from "../components/panel.jsx";
import { Button, OperatorButton } from "../components/buttons.jsx";
import {
  ErrorBanner,
  SkeletonBlock,
  EmptyState,
} from "../components/states.jsx";
import DebatePanel from "../components/DebatePanel.jsx";

// Effective states from which the certify/reject/reject-with-changes
// actions are valid. Mirrors backend ``_CERTIFICATION_SOURCE_STATES``.
const CERTIFICATION_SOURCE_STATES = new Set([
  "in_review",
  "preview_ready",
  "code_reviewed",
]);

// Effective states from which preview deploy is allowed. Mirrors backend
// ``preview_deploy._DEPLOY_PREVIEW_SOURCE_STATES``. The button is hidden for
// any state outside this set — including merged/certified/archived which the
// backend also flatly refuses.
const PREVIEW_DEPLOY_SOURCE_STATES = new Set([
  "in_review",
  "preview_pending",
  "preview_ready",
  "code_reviewed",
  "changes_requested",
  "review_failed",
  "needs_rework",
]);

// Effective states from which the Merge button is shown. The backend also
// enforces operator_certified AND ready_to_merge as a hard gate; the UI is
// stricter here so a half-certified item does not advertise Merge.
const MERGE_SOURCE_STATES = new Set(["ready_to_merge", "certified", "blocked_merge"]);

// WI #002 was merged/completed before the merge/complete actions existed.
// Hide the new buttons for it so operators do not accidentally re-merge.
const PRE_SLICE5_LANDED_IDS = new Set([2]);

const CERTIFICATION_ACTIONS = {
  certify: {
    headline: "CERTIFY WORK ITEM",
    body:
      "Mark this Work Item as operator-certified. A note is optional and " +
      "will be shown on the item.",
    confirmVariant: "success",
    confirmLabel: "CERTIFY",
    inputLabel: "CERTIFICATION NOTE (OPTIONAL)",
    placeholder: "Optional context (e.g. 'Smoke tested in staging')",
    required: false,
  },
  reject: {
    headline: "REJECT WORK ITEM",
    body:
      "Reject this Work Item outright. The work will be abandoned. A " +
      "reason is required and will be visible to other operators.",
    confirmVariant: "danger",
    confirmLabel: "REJECT",
    inputLabel: "REJECTION REASON (REQUIRED)",
    placeholder: "Why is this being rejected?",
    required: true,
  },
  reject_with_changes: {
    headline: "REQUEST CHANGES",
    body:
      "Send this Work Item back to the builder with a change request. " +
      "Describe what needs to change before it can be re-reviewed.",
    confirmVariant: "danger",
    confirmLabel: "REQUEST CHANGES",
    inputLabel: "CHANGE REQUEST (REQUIRED)",
    placeholder: "What needs to change?",
    required: true,
  },
};

function CertificationActionModal({ action, busy, onConfirm, onCancel }) {
  const [text, setText] = useState("");
  const copy = action ? CERTIFICATION_ACTIONS[action] : null;

  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onCancel?.();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  useEffect(() => {
    setText("");
  }, [action]);

  if (!copy) return null;
  const disabled = busy || (copy.required && !text.trim());

  return (
    <div
      className="fixed inset-0 z-50 flex items-end md:items-center justify-center bg-canvas/80 backdrop-blur-sm p-4"
      onClick={busy ? undefined : onCancel}
    >
      <div
        className="w-full max-w-lg border border-edge-strong bg-surface shadow-inset"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="cert-modal-headline"
      >
        <header className="flex items-center justify-between gap-3 border-b border-edge px-4 py-3">
          <div>
            <div className="label-tel-strong text-fg-primary">[ CONFIRM ]</div>
            <h2
              id="cert-modal-headline"
              className="mt-1 font-display text-lg font-bold tracking-tight"
            >
              {copy.headline}
            </h2>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="font-mono text-fg-muted hover:text-fg-primary disabled:opacity-40"
            aria-label="Close"
          >
            ✕
          </button>
        </header>

        <div className="px-4 py-4 space-y-3">
          <p className="text-sm text-fg-secondary">{copy.body}</p>
          <div className="space-y-1">
            <label className="label-tel block" htmlFor="cert-modal-input">
              {copy.inputLabel}
            </label>
            <textarea
              id="cert-modal-input"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={copy.placeholder}
              rows={4}
              disabled={busy}
              className="w-full px-3 py-2 text-sm bg-canvas border border-edge outline-none focus:border-edge-strong font-mono disabled:opacity-60"
            />
          </div>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-edge px-4 py-3">
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            CANCEL
          </Button>
          <Button
            variant={copy.confirmVariant}
            onClick={() => onConfirm(text.trim())}
            disabled={disabled}
          >
            {busy ? "WORKING…" : copy.confirmLabel}
          </Button>
        </footer>
      </div>
    </div>
  );
}

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
  const [certAction, setCertAction] = useState(null); // 'certify' | 'reject' | 'reject_with_changes' | null
  const [certBusy, setCertBusy] = useState(false);
  const [repoLocks, setRepoLocks] = useState([]);
  const [gateError, setGateError] = useState(null);
  const [releasingLockId, setReleasingLockId] = useState(null);
  const [previewBusy, setPreviewBusy] = useState(null); // 'deploy' | 'revert' | null
  const [showRevertInput, setShowRevertInput] = useState(false);
  const [revertReason, setRevertReason] = useState("");
  const [showMergeModal, setShowMergeModal] = useState(false);
  const [mergeBaseBranch, setMergeBaseBranch] = useState("");
  const [mergeNote, setMergeNote] = useState("");
  const [mergeBusy, setMergeBusy] = useState(false);
  const [completeBusy, setCompleteBusy] = useState(false);
  const [completionNote, setCompletionNote] = useState("");
  const [showCompleteInput, setShowCompleteInput] = useState(false);

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
    getActiveRepoLocks()
      .then(setRepoLocks)
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
    setGateError(null);
    postJson(`/builder/work-items/${id}/start-build`, {})
      .then((data) => {
        setBuilderTask(data);
        refresh();
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 409) {
          const detail = err.body && err.body.detail ? err.body.detail : err.body;
          if (detail && typeof detail === "object" && detail.blocker_code) {
            setGateError(detail);
            return;
          }
        }
        setError(err.message);
      })
      .finally(() => setBuilderBusy(false));
  }

  function clearLock(lockId) {
    setReleasingLockId(lockId);
    releaseRepoLock(lockId)
      .then(() => {
        setGateError(null);
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setReleasingLockId(null));
  }

  function syncBuilder() {
    if (!builderTask) return;
    setBuilderBusy(true);
    postJson(`/builder/tasks/${builderTask.id}/sync`, {})
      .then((data) => setBuilderTask(data))
      .catch((err) => setError(err.message))
      .finally(() => setBuilderBusy(false));
  }

  function runDeployPreview() {
    setPreviewBusy("deploy");
    setGateError(null);
    setError(null);
    deployPreview(id, null)
      .then(() => refresh())
      .catch((err) => {
        if (err instanceof ApiError && err.status === 409 && err.body) {
          const detail = err.body.detail ?? err.body;
          if (detail && typeof detail === "object" && detail.blocker_code) {
            setGateError(detail);
            return;
          }
        }
        setError(err.message);
      })
      .finally(() => setPreviewBusy(null));
  }

  function runRevertPreview() {
    if (!revertReason.trim()) return;
    setPreviewBusy("revert");
    setGateError(null);
    setError(null);
    revertPreview(id, revertReason.trim(), null)
      .then(() => {
        setShowRevertInput(false);
        setRevertReason("");
        refresh();
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 409 && err.body) {
          const detail = err.body.detail ?? err.body;
          if (detail && typeof detail === "object" && detail.blocker_code) {
            setGateError(detail);
            return;
          }
        }
        setError(err.message);
      })
      .finally(() => setPreviewBusy(null));
  }

  function runMerge() {
    if (!item || !mergeBaseBranch.trim()) return;
    setMergeBusy(true);
    setGateError(null);
    setError(null);
    mergeWorkItem(id, {
      prNumber: item.pr_number,
      branch: item.branch_name,
      baseBranch: mergeBaseBranch.trim(),
      mergeNote: mergeNote.trim() || null,
    })
      .then(() => {
        setShowMergeModal(false);
        setMergeBaseBranch("");
        setMergeNote("");
        refresh();
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 409 && err.body) {
          const detail = err.body.detail ?? err.body;
          if (detail && typeof detail === "object" && detail.blocker_code) {
            setGateError(detail);
            return;
          }
        }
        setError(err.message);
      })
      .finally(() => setMergeBusy(false));
  }

  function runComplete() {
    setCompleteBusy(true);
    setError(null);
    completeWorkItem(id, { completionNote: completionNote.trim() || null })
      .then(() => {
        setShowCompleteInput(false);
        setCompletionNote("");
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setCompleteBusy(false));
  }

  function submitCertificationAction(text) {
    if (!certAction) return;
    setCertBusy(true);
    setError(null);
    const promise =
      certAction === "certify"
        ? certifyWorkItem(id, text || null)
        : certAction === "reject"
        ? rejectWorkItem(id, text)
        : rejectWorkItemWithChanges(id, text);
    promise
      .then(() => {
        setCertAction(null);
        refresh();
      })
      .catch((err) => setError(err.message))
      .finally(() => setCertBusy(false));
  }

  const canStartBuild = item?.approved_by_operator && !builderTask;
  const blockedTypes = new Set(["note"]);
  const canStartBuildType = !blockedTypes.has(item?.type?.toLowerCase());
  const canCertify =
    !!item && CERTIFICATION_SOURCE_STATES.has(item.effective_state);

  // Preview deploy is shown only when the Work Item has the branch/PR
  // metadata the backend requires AND its effective state allows a deploy.
  // Once deployed, the Revert button replaces Deploy. Both controls stay
  // hidden for merged/certified/ready_to_merge/archived items — the
  // backend would 409 anyway, and the UI should not advertise them.
  const hasPreviewMetadata =
    !!item && !!item.branch_name && item.pr_number != null;
  const previewStateAllowsDeploy =
    !!item && PREVIEW_DEPLOY_SOURCE_STATES.has(item.effective_state);
  const canDeployPreview =
    hasPreviewMetadata && previewStateAllowsDeploy && !item.preview_deployed;
  const canRevertPreview =
    !!item && item.preview_deployed === true && previewStateAllowsDeploy;

  // Merge / complete (slice 5). Hide for WI-002 (pre-slice-5 landed) and
  // anything missing the PR metadata the backend requires. The backend
  // additionally enforces operator_certified AND ready_to_merge; the UI
  // surfaces the button only when both flags are true so a half-certified
  // item does not get a misleading control.
  const itemId = item ? Number(item.id) : null;
  const isPreSlice5Landed =
    itemId != null && PRE_SLICE5_LANDED_IDS.has(itemId);
  const hasMergeMetadata =
    !!item && !!item.branch_name && item.pr_number != null;
  const canMerge =
    !!item &&
    !isPreSlice5Landed &&
    hasMergeMetadata &&
    item.operator_certified === true &&
    item.ready_to_merge === true &&
    MERGE_SOURCE_STATES.has(item.effective_state);
  const canComplete =
    !!item &&
    !isPreSlice5Landed &&
    item.effective_state === "merged" &&
    !!item.merge_commit_sha &&
    (item.post_merge_health_status || "").toLowerCase() === "healthy";
  const isComplete = !!item && item.effective_state === "complete";

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
            <StatusBadge status={item.effective_state || item.status} />
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
          {canCertify && (
            <>
              <Button
                variant="success"
                onClick={() => setCertAction("certify")}
              >
                CERTIFY
              </Button>
              <Button
                variant="danger"
                onClick={() => setCertAction("reject_with_changes")}
              >
                REQUEST CHANGES
              </Button>
              <Button
                variant="danger"
                onClick={() => setCertAction("reject")}
              >
                REJECT
              </Button>
            </>
          )}
          {canDeployPreview && (
            <Button
              variant="success"
              onClick={runDeployPreview}
              disabled={previewBusy === "deploy"}
              title="Build and bring up this PR branch on the compose stack"
            >
              {previewBusy === "deploy" ? "DEPLOYING…" : "DEPLOY PREVIEW"}
            </Button>
          )}
          {canRevertPreview && !showRevertInput && (
            <Button
              variant="danger"
              onClick={() => setShowRevertInput(true)}
              disabled={previewBusy === "revert"}
              title="Revert the preview back to the project's base branch"
            >
              REVERT PREVIEW
            </Button>
          )}
          {canMerge && (
            <Button
              variant="success"
              onClick={() => setShowMergeModal(true)}
              title="Merge the PR and deploy the base branch"
            >
              MERGE
            </Button>
          )}
          {canComplete && !showCompleteInput && (
            <Button
              variant="success"
              onClick={() => setShowCompleteInput(true)}
              title="Mark this work item as complete"
            >
              COMPLETE
            </Button>
          )}
          {isComplete && (
            <span
              className="px-2 py-1 text-xs font-mono uppercase tracking-telemetry text-st-completed border border-st-completed"
              title="Work item is complete"
            >
              ✓ COMPLETE
            </span>
          )}
        </div>
      </div>

      {canComplete && showCompleteInput && (
        <div className="border border-edge bg-surface px-4 py-3 space-y-2">
          <div className="label-tel-strong text-fg-primary">
            [ COMPLETE WORK ITEM — POST-MERGE VERIFIED ]
          </div>
          <textarea
            value={completionNote}
            onChange={(e) => setCompletionNote(e.target.value)}
            placeholder="Optional completion note (recorded on the work item)"
            rows={3}
            disabled={completeBusy}
            className="w-full px-3 py-2 text-sm bg-canvas border border-edge outline-none focus:border-edge-strong font-mono disabled:opacity-60"
          />
          <div className="flex items-center gap-2">
            <Button
              variant="success"
              onClick={runComplete}
              disabled={completeBusy}
            >
              {completeBusy ? "COMPLETING…" : "CONFIRM COMPLETE"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setShowCompleteInput(false);
                setCompletionNote("");
              }}
              disabled={completeBusy}
            >
              CANCEL
            </Button>
          </div>
        </div>
      )}

      {showMergeModal && item && (
        <div
          className="fixed inset-0 z-50 flex items-end md:items-center justify-center bg-canvas/80 backdrop-blur-sm p-4"
          onClick={mergeBusy ? undefined : () => setShowMergeModal(false)}
        >
          <div
            className="w-full max-w-lg border border-edge-strong bg-surface shadow-inset"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
            aria-labelledby="merge-modal-headline"
          >
            <header className="flex items-center justify-between gap-3 border-b border-edge px-4 py-3">
              <div>
                <div className="label-tel-strong text-fg-primary">[ CONFIRM ]</div>
                <h2
                  id="merge-modal-headline"
                  className="mt-1 font-display text-lg font-bold tracking-tight"
                >
                  MERGE WORK ITEM
                </h2>
              </div>
              <button
                type="button"
                onClick={() => setShowMergeModal(false)}
                disabled={mergeBusy}
                className="font-mono text-fg-muted hover:text-fg-primary disabled:opacity-40"
                aria-label="Close"
              >
                ✕
              </button>
            </header>

            <div className="px-4 py-4 space-y-3">
              <p className="text-sm text-fg-secondary">
                This will merge PR #{item.pr_number} ({item.branch_name})
                into the supplied base branch via the host executor, then
                rebuild and bring up the dashboard service. The merge SHA
                is recorded on success.
              </p>
              <div className="space-y-1">
                <label className="label-tel block" htmlFor="merge-base-input">
                  EXPECTED BASE BRANCH (REQUIRED)
                </label>
                <input
                  id="merge-base-input"
                  type="text"
                  value={mergeBaseBranch}
                  onChange={(e) => setMergeBaseBranch(e.target.value)}
                  placeholder="e.g. main"
                  disabled={mergeBusy}
                  className="w-full px-3 py-2 text-sm bg-canvas border border-edge outline-none focus:border-edge-strong font-mono disabled:opacity-60"
                />
              </div>
              <div className="space-y-1">
                <label className="label-tel block" htmlFor="merge-note-input">
                  MERGE NOTE (OPTIONAL)
                </label>
                <textarea
                  id="merge-note-input"
                  value={mergeNote}
                  onChange={(e) => setMergeNote(e.target.value)}
                  placeholder="Optional note (recorded on the work item)"
                  rows={3}
                  disabled={mergeBusy}
                  className="w-full px-3 py-2 text-sm bg-canvas border border-edge outline-none focus:border-edge-strong font-mono disabled:opacity-60"
                />
              </div>
            </div>

            <footer className="flex items-center justify-end gap-2 border-t border-edge px-4 py-3">
              <Button
                variant="ghost"
                onClick={() => setShowMergeModal(false)}
                disabled={mergeBusy}
              >
                CANCEL
              </Button>
              <Button
                variant="success"
                onClick={runMerge}
                disabled={mergeBusy || !mergeBaseBranch.trim()}
              >
                {mergeBusy ? "MERGING…" : "CONFIRM MERGE"}
              </Button>
            </footer>
          </div>
        </div>
      )}

      {canRevertPreview && showRevertInput && (
        <div className="border border-edge bg-surface px-4 py-3 space-y-2">
          <div className="label-tel-strong text-fg-primary">
            [ REVERT PREVIEW — REASON REQUIRED ]
          </div>
          <textarea
            value={revertReason}
            onChange={(e) => setRevertReason(e.target.value)}
            placeholder="Why are you reverting? (recorded on the work item)"
            rows={3}
            disabled={previewBusy === "revert"}
            className="w-full px-3 py-2 text-sm bg-canvas border border-edge outline-none focus:border-edge-strong font-mono disabled:opacity-60"
          />
          <div className="flex items-center gap-2">
            <Button
              variant="danger"
              onClick={runRevertPreview}
              disabled={previewBusy === "revert" || !revertReason.trim()}
            >
              {previewBusy === "revert" ? "REVERTING…" : "CONFIRM REVERT"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => {
                setShowRevertInput(false);
                setRevertReason("");
              }}
              disabled={previewBusy === "revert"}
            >
              CANCEL
            </Button>
          </div>
        </div>
      )}

      {error && item && <ErrorBanner message={error} />}

      {gateError && (
        <div className="border border-st-blocked bg-canvas/80 px-4 py-3 space-y-2">
          <div className="flex items-center justify-between">
            <div className="label-tel-strong text-st-blocked">
              [ BUILD GATE BLOCKED — {gateError.blocker_code?.toUpperCase()} ]
            </div>
            <button
              type="button"
              onClick={() => setGateError(null)}
              className="font-mono text-fg-muted hover:text-fg-primary"
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
          <p className="text-sm text-fg-primary whitespace-pre-wrap">
            {gateError.blocker_message || "Build was refused by the safety gate."}
          </p>
          {gateError.repo_path && (
            <div className="text-xs font-mono text-fg-secondary">
              REPO: {gateError.repo_path}
            </div>
          )}
          {Array.isArray(gateError.dirty_files) && gateError.dirty_files.length > 0 && (
            <div className="text-xs font-mono text-fg-secondary">
              DIRTY: {gateError.dirty_files.slice(0, 5).join(", ")}
              {gateError.dirty_files.length > 5 ? ` (+${gateError.dirty_files.length - 5})` : ""}
            </div>
          )}
          {Array.isArray(gateError.staged_files) && gateError.staged_files.length > 0 && (
            <div className="text-xs font-mono text-fg-secondary">
              STAGED: {gateError.staged_files.slice(0, 5).join(", ")}
              {gateError.staged_files.length > 5 ? ` (+${gateError.staged_files.length - 5})` : ""}
            </div>
          )}
          {Array.isArray(gateError.untracked_files) && gateError.untracked_files.length > 0 && (
            <div className="text-xs font-mono text-fg-secondary">
              UNTRACKED: {gateError.untracked_files.slice(0, 5).join(", ")}
              {gateError.untracked_files.length > 5 ? ` (+${gateError.untracked_files.length - 5})` : ""}
            </div>
          )}
          {gateError.existing_lock_id != null && (
            <div className="text-xs font-mono text-fg-secondary">
              EXISTING LOCK #{gateError.existing_lock_id}
              {gateError.existing_lock_branch ? ` @ ${gateError.existing_lock_branch}` : ""}
              {gateError.existing_lock_work_item_id != null
                ? ` (WI-${gateError.existing_lock_work_item_id})`
                : ""}
            </div>
          )}
        </div>
      )}

      {repoLocks.length > 0 && (
        <Panel title="REPO LOCKS" subtitle="// active build locks across repos">
          <div className="space-y-2">
            {repoLocks.map((lock) => (
              <div
                key={lock.id}
                className="flex items-center justify-between gap-3 px-3 py-2 bg-canvas border border-edge"
              >
                <div className="min-w-0 space-y-1">
                  <div className="text-sm font-mono text-fg-primary truncate">
                    {lock.repo_path}
                  </div>
                  <div className="text-xs font-mono text-fg-secondary">
                    BRANCH {lock.branch_name} · COMMIT {lock.commit_sha.slice(0, 8)}
                    {lock.work_item_id != null ? ` · WI-${lock.work_item_id}` : ""}
                    {lock.task_id ? ` · TASK ${lock.task_id}` : ""}
                  </div>
                  <div className="text-xs font-mono text-fg-muted">
                    SINCE {formatTime(lock.started_at)}
                    {lock.lock_owner ? ` · OWNER ${lock.lock_owner}` : ""}
                  </div>
                </div>
                <Button
                  variant="danger"
                  onClick={() => clearLock(lock.id)}
                  disabled={releasingLockId === lock.id}
                >
                  {releasingLockId === lock.id ? "CLEARING…" : "CLEAR LOCK"}
                </Button>
              </div>
            ))}
          </div>
        </Panel>
      )}

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

      {(item.preview_status ||
        item.preview_deployed ||
        item.preview_reverted_at ||
        item.preview_error) && (
        <Panel title="PREVIEW DEPLOYMENT" subtitle="// branch-on-compose state">
          <div className="grid grid-cols-2 gap-3">
            <FieldRow
              label="STATUS"
              value={item.preview_status?.toUpperCase() || "—"}
              mono
            />
            <FieldRow
              label="HEALTH"
              value={item.preview_health_status?.toUpperCase() || "—"}
              mono
            />
            <FieldRow
              label="BRANCH"
              value={item.preview_branch || "—"}
              mono
            />
            <FieldRow
              label="PR #"
              value={item.preview_pr_number ?? "—"}
              mono
            />
            <FieldRow
              label="COMMIT"
              value={item.preview_commit_sha ? item.preview_commit_sha.slice(0, 8) : "—"}
              mono
            />
            <FieldRow
              label="DEPLOYED AT"
              value={formatTime(item.preview_deployed_at) || "—"}
              mono
            />
            <FieldRow
              label="DEPLOYED BY"
              value={item.preview_deployed_by || "—"}
              mono
            />
            <FieldRow
              label="URL"
              value={item.preview_url || "—"}
              mono
            />
            <FieldRow
              label="REVERTED AT"
              value={formatTime(item.preview_reverted_at) || "—"}
              mono
            />
            <FieldRow
              label="REVERTED BY"
              value={item.preview_reverted_by || "—"}
              mono
            />
          </div>
          {item.preview_revert_reason && (
            <div className="mt-3 text-xs text-fg-muted border border-edge bg-canvas/50 p-3">
              <div className="label-tel mb-1">REVERT REASON</div>
              <p className="whitespace-pre-wrap font-mono">
                {item.preview_revert_reason}
              </p>
            </div>
          )}
          {item.preview_error && (
            <div className="mt-3 text-xs border border-st-blocked bg-canvas/80 p-3">
              <div className="label-tel-strong mb-1 text-st-blocked">
                PREVIEW ERROR
              </div>
              <p className="whitespace-pre-wrap font-mono text-fg-primary">
                {item.preview_error}
              </p>
            </div>
          )}
        </Panel>
      )}

      {(item.merge_commit_sha ||
        item.merge_status ||
        item.merge_error ||
        item.completed_at) && (
        <Panel title="MERGE / COMPLETE" subtitle="// slice 5 terminal state">
          <div className="grid grid-cols-2 gap-3">
            <FieldRow
              label="MERGE STATUS"
              value={item.merge_status?.toUpperCase() || "—"}
              mono
            />
            <FieldRow
              label="POST-MERGE HEALTH"
              value={item.post_merge_health_status?.toUpperCase() || "—"}
              mono
            />
            <FieldRow
              label="MERGE SHA"
              value={item.merge_commit_sha ? item.merge_commit_sha.slice(0, 12) : "—"}
              mono
            />
            <FieldRow
              label="MERGED AT"
              value={formatTime(item.merged_at) || "—"}
              mono
            />
            <FieldRow
              label="MERGED BY"
              value={item.merged_by || "—"}
              mono
            />
            <FieldRow
              label="VERIFIED AT"
              value={formatTime(item.post_merge_verified_at) || "—"}
              mono
            />
            <FieldRow
              label="COMPLETED AT"
              value={formatTime(item.completed_at) || "—"}
              mono
            />
            <FieldRow
              label="COMPLETED BY"
              value={item.completed_by || "—"}
              mono
            />
          </div>
          {item.merge_note && (
            <div className="mt-3 text-xs text-fg-muted border border-edge bg-canvas/50 p-3">
              <div className="label-tel mb-1">MERGE NOTE</div>
              <p className="whitespace-pre-wrap font-mono">{item.merge_note}</p>
            </div>
          )}
          {item.completion_note && (
            <div className="mt-3 text-xs text-fg-muted border border-edge bg-canvas/50 p-3">
              <div className="label-tel mb-1">COMPLETION NOTE</div>
              <p className="whitespace-pre-wrap font-mono">{item.completion_note}</p>
            </div>
          )}
          {item.merge_error && (
            <div className="mt-3 text-xs border border-st-blocked bg-canvas/80 p-3">
              <div className="label-tel-strong mb-1 text-st-blocked">
                MERGE ERROR
              </div>
              <p className="whitespace-pre-wrap font-mono text-fg-primary">
                {item.merge_error}
              </p>
            </div>
          )}
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

      <CertificationActionModal
        action={certAction}
        busy={certBusy}
        onConfirm={submitCertificationAction}
        onCancel={() => {
          if (!certBusy) setCertAction(null);
        }}
      />
    </div>
  );
}
