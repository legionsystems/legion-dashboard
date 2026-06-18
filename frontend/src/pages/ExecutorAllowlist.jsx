import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  getJson,
  postJson,
  patchJson,
  deleteRequest,
} from "../api/client.js";

function Section({ title, children, action }) {
  return (
    <div className="border border-edge bg-raised rounded-md p-4 mb-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-mono uppercase tracking-telemetry text-xs font-semibold text-fg-primary">
          {title}
        </h3>
        {action}
      </div>
      {children}
    </div>
  );
}

function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  type = "button",
  size = "md",
}) {
  const base =
    "font-mono uppercase tracking-telemetry text-[10px] font-semibold rounded border transition-colors";
  const sizes = { sm: "px-2 py-1 text-[9px]", md: "px-4 py-2" };
  const variants = {
    primary:
      "border-fg-primary text-fg-primary hover:bg-fg-primary hover:text-canvas",
    secondary:
      "border-edge text-fg-secondary hover:border-fg-secondary hover:text-fg-primary",
    danger: "border-red-500 text-red-400 hover:bg-red-500 hover:text-canvas",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${sizes[size]} ${variants[variant]} ${
        disabled ? "opacity-50 cursor-not-allowed" : ""
      }`}
    >
      {children}
    </button>
  );
}

function TextInput({ value, onChange, placeholder, disabled }) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className="w-full bg-canvas border border-edge rounded px-2 py-1.5 font-mono text-xs text-fg-primary focus:outline-none focus:border-fg-primary"
    />
  );
}

function Badge({ children, tone = "neutral" }) {
  const tones = {
    neutral: "border-edge text-fg-secondary",
    good: "border-green-500 text-green-400",
    warn: "border-amber-500 text-amber-400",
    bad: "border-red-500 text-red-400",
  };
  return (
    <span
      className={`inline-block border ${tones[tone]} px-1.5 py-0.5 font-mono uppercase tracking-telemetry text-[9px]`}
    >
      {children}
    </span>
  );
}

function ConfirmModal({ open, onClose, onConfirm, busy }) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/80">
      <div className="border border-edge bg-raised rounded-md p-5 max-w-md w-full">
        <h4 className="font-mono uppercase tracking-telemetry text-xs font-semibold text-fg-primary mb-3">
          Apply allowlist?
        </h4>
        <p className="font-mono text-xs text-fg-secondary mb-4">
          This writes the new allowlist to the host config file and runs{" "}
          <span className="text-fg-primary">
            systemctl restart legion-preview-executor
          </span>
          . The executor will be briefly unavailable. Continue?
        </p>
        <div className="flex justify-end gap-2">
          <Button onClick={onClose} variant="secondary" disabled={busy}>
            Cancel
          </Button>
          <Button onClick={onConfirm} variant="primary" disabled={busy}>
            {busy ? "Applying..." : "Apply & restart"}
          </Button>
        </div>
      </div>
    </div>
  );
}

export default function ExecutorAllowlist() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [config, setConfig] = useState(null);
  const [addPath, setAddPath] = useState("");
  const [addNote, setAddNote] = useState("");
  const [adding, setAdding] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    load();
  }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await getJson("/executor-allowlist");
      setConfig(data);
    } catch (err) {
      setError(`Failed to load allowlist: ${err.message}`);
    } finally {
      setLoading(false);
    }
  }

  async function handleAdd(e) {
    e.preventDefault();
    setAdding(true);
    setError(null);
    try {
      await postJson("/executor-allowlist", {
        path: addPath.trim(),
        note: addNote.trim() || null,
      });
      setAddPath("");
      setAddNote("");
      await load();
    } catch (err) {
      setError(`Failed to add: ${err.body?.detail || err.message}`);
    } finally {
      setAdding(false);
    }
  }

  async function handleToggle(root) {
    setError(null);
    try {
      await patchJson(`/executor-allowlist/${root.id}`, {
        is_enabled: !root.is_enabled,
      });
      await load();
    } catch (err) {
      setError(`Failed to update: ${err.body?.detail || err.message}`);
    }
  }

  async function handleRemove(root) {
    if (
      !window.confirm(
        `Remove "${root.path}" from the allowlist? This is reversible — you can re-add it.`,
      )
    ) {
      return;
    }
    setError(null);
    try {
      await deleteRequest(`/executor-allowlist/${root.id}`);
      await load();
    } catch (err) {
      setError(`Failed to remove: ${err.body?.detail || err.message}`);
    }
  }

  async function handleApply() {
    setApplying(true);
    setError(null);
    try {
      await postJson("/executor-allowlist/apply", { confirm: true });
      setApplyOpen(false);
      await load();
    } catch (err) {
      setError(`Apply failed: ${err.body?.detail || err.message}`);
    } finally {
      setApplying(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="font-mono text-xs text-fg-secondary">
          Loading executor allowlist...
        </span>
      </div>
    );
  }

  if (!config) {
    return (
      <div className="border border-red-500 bg-red-500/10 rounded p-3">
        <p className="font-mono text-xs text-red-400">{error}</p>
      </div>
    );
  }

  const enabledRoots = config.roots.filter((r) => r.is_enabled);

  return (
    <div className="max-w-4xl">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="font-display font-extrabold tracking-tighter-display text-fg-primary text-2xl mb-1">
            EXECUTOR ALLOWLIST
          </h1>
          <p className="font-mono text-xs text-fg-muted">
            Repo roots the host preview executor will accept. Changes are
            staged until Apply restarts the service.
          </p>
        </div>
        <Button
          onClick={() => navigate("/settings")}
          variant="secondary"
          size="sm"
        >
          Back to Settings
        </Button>
      </div>

      {error && (
        <div className="mb-4 border border-red-500 bg-red-500/10 rounded p-3">
          <p className="font-mono text-xs text-red-400">{error}</p>
        </div>
      )}

      {config.env_override_active && (
        <div className="mb-4 border border-amber-500 bg-amber-500/10 rounded p-3">
          <p className="font-mono text-xs text-amber-400 mb-1">
            LEGION_EXECUTOR_ALLOWED_REPO_ROOTS env override is set on the
            host.
          </p>
          <p className="font-mono text-[10px] text-fg-muted">
            The executor will use the env var verbatim and ignore the
            dashboard's persisted list until the override is removed from{" "}
            <span className="text-fg-secondary">
              /etc/legion/preview-executor.env
            </span>
            .
          </p>
          {config.env_override_value && (
            <p className="font-mono text-[10px] text-fg-secondary mt-1 break-all">
              Current value: {config.env_override_value}
            </p>
          )}
        </div>
      )}

      {config.pending_apply && (
        <div className="mb-4 border border-amber-500 bg-amber-500/10 rounded p-3">
          <p className="font-mono text-xs text-amber-400">
            Pending changes — the on-disk config does not match the
            persisted list. Click Apply to write{" "}
            {config.config_path} and restart the executor.
          </p>
        </div>
      )}

      {config.last_apply_at && (
        <div
          className={`mb-4 border rounded p-3 ${
            config.last_apply_restart_ok
              ? "border-green-500 bg-green-500/10"
              : "border-red-500 bg-red-500/10"
          }`}
        >
          <p
            className={`font-mono text-xs ${
              config.last_apply_restart_ok
                ? "text-green-400"
                : "text-red-400"
            }`}
          >
            Last apply: {new Date(config.last_apply_at).toLocaleString()} —{" "}
            {config.last_apply_restart_ok ? "restart OK" : "restart failed"}
          </p>
          {config.last_apply_error && (
            <p className="font-mono text-[10px] text-fg-muted mt-1 break-words">
              {config.last_apply_error}
            </p>
          )}
        </div>
      )}

      <Section title={`Allowed Repo Roots (${enabledRoots.length} enabled)`}>
        <div className="space-y-2">
          {config.roots.map((root) => (
            <div
              key={root.id}
              className="flex items-center justify-between border border-edge bg-canvas rounded p-3"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-xs text-fg-primary break-all">
                    {root.path}
                  </span>
                  {root.is_default && <Badge tone="neutral">default</Badge>}
                  {root.is_enabled ? (
                    <Badge tone="good">enabled</Badge>
                  ) : (
                    <Badge tone="warn">disabled</Badge>
                  )}
                </div>
                {root.note && (
                  <p className="font-mono text-[10px] text-fg-muted mt-1 break-words">
                    {root.note}
                  </p>
                )}
                <p className="font-mono text-[9px] text-fg-muted mt-0.5">
                  Added {new Date(root.added_at).toLocaleString()}
                  {root.added_by ? ` by ${root.added_by}` : ""}
                </p>
              </div>
              <div className="flex flex-col items-end gap-1 shrink-0 ml-3">
                <Button
                  onClick={() => handleToggle(root)}
                  variant="secondary"
                  size="sm"
                >
                  {root.is_enabled ? "Disable" : "Enable"}
                </Button>
                {!root.is_default && (
                  <Button
                    onClick={() => handleRemove(root)}
                    variant="danger"
                    size="sm"
                  >
                    Remove
                  </Button>
                )}
              </div>
            </div>
          ))}
          {config.roots.length === 0 && (
            <p className="font-mono text-xs text-fg-muted">
              No roots configured. Add at least one before applying.
            </p>
          )}
        </div>
      </Section>

      <Section title="Add Root">
        <form onSubmit={handleAdd} className="space-y-3">
          <div>
            <label className="block font-mono uppercase tracking-telemetry text-[10px] text-fg-secondary mb-1">
              Path
            </label>
            <TextInput
              value={addPath}
              onChange={setAddPath}
              placeholder="/srv/repo/new-app"
              disabled={adding}
            />
            <p className="font-mono text-[10px] text-fg-muted mt-1">
              Absolute path, no symlinks, no ../. Cannot be under /root,
              /etc, /tmp, /var, /usr, etc.
            </p>
          </div>
          <div>
            <label className="block font-mono uppercase tracking-telemetry text-[10px] text-fg-secondary mb-1">
              Note (optional)
            </label>
            <TextInput
              value={addNote}
              onChange={setAddNote}
              placeholder="onboarding lgn-newapp"
              disabled={adding}
            />
          </div>
          <Button type="submit" disabled={adding || !addPath.trim()}>
            {adding ? "Adding..." : "Add"}
          </Button>
        </form>
      </Section>

      <div className="flex justify-end gap-2 mt-4">
        <Button
          onClick={() => setApplyOpen(true)}
          variant="primary"
          disabled={!config.pending_apply}
        >
          {config.pending_apply ? "Apply changes" : "Up to date"}
        </Button>
      </div>

      <ConfirmModal
        open={applyOpen}
        onClose={() => setApplyOpen(false)}
        onConfirm={handleApply}
        busy={applying}
      />
    </div>
  );
}
