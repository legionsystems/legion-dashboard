import { useEffect, useState } from "react";
import { getJson, postJson, putJson } from "../api/client.js";

function Section({ title, children }) {
  return (
    <div className="border border-edge bg-raised rounded-md p-4 mb-4">
      <h3 className="font-mono uppercase tracking-telemetry text-xs font-semibold text-fg-primary mb-3">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Field({ label, hint, error, children }) {
  return (
    <div className="mb-3">
      <label className="block font-mono uppercase tracking-telemetry text-[10px] text-fg-secondary mb-1">
        {label}
      </label>
      {children}
      {hint && !error && (
        <p className="font-mono text-[10px] text-fg-muted mt-1">{hint}</p>
      )}
      {error && <p className="font-mono text-[10px] text-red-400 mt-1">{error}</p>}
    </div>
  );
}

function TextInput({ value, onChange, type = "text", placeholder, disabled }) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className="w-full bg-canvas border border-edge rounded px-2 py-1.5 font-mono text-xs text-fg-primary focus:outline-none focus:border-fg-primary"
    />
  );
}

function SelectInput({ value, onChange, options, disabled }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className="w-full bg-canvas border border-edge rounded px-2 py-1.5 font-mono text-xs text-fg-primary focus:outline-none focus:border-fg-primary"
    >
      <option value="">-- Select --</option>
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      disabled={disabled}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
        checked ? "bg-fg-primary" : "bg-edge"
      } ${disabled ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
    >
      <span
        className={`inline-block h-3.5 w-3.5 transform rounded-full bg-canvas transition-transform ${
          checked ? "translate-x-5" : "translate-x-1"
        }`}
      />
    </button>
  );
}

function Button({ children, onClick, variant = "primary", disabled, type = "button", size = "md" }) {
  const base = "font-mono uppercase tracking-telemetry text-[10px] font-semibold rounded border transition-colors";
  const sizes = {
    sm: "px-2 py-1 text-[9px]",
    md: "px-4 py-2",
  };
  const variants = {
    primary: "border-fg-primary text-fg-primary hover:bg-fg-primary hover:text-canvas",
    secondary: "border-edge text-fg-secondary hover:border-fg-secondary hover:text-fg-primary",
    danger: "border-red-500 text-red-400 hover:bg-red-500 hover:text-canvas",
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${sizes[size]} ${variants[variant]} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
    >
      {children}
    </button>
  );
}

function HostCard({ host, onEdit, onTest, onRefresh, onDelete }) {
  const [testing, setTesting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [testResult, setTestResult] = useState(null);

  const isHermes = host.source === "hermes";

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await postJson(`/settings/model-hosts/${host.id}/test`);
      setTestResult(result);
    } catch (err) {
      setTestResult({ success: false, error: err.message });
    } finally {
      setTesting(false);
    }
  }

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await postJson(`/settings/model-hosts/${host.id}/refresh-models`);
      onRefresh(host.id);
    } catch (err) {
      // Error handled by parent
    } finally {
      setRefreshing(false);
    }
  }

  const statusColor = host.last_test_status === "success" ? "text-green-400" :
    host.last_test_status === "failed" ? "text-red-400" : "text-fg-muted";

  return (
    <div className="border border-edge bg-canvas rounded p-3 mb-3">
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="flex items-center gap-2">
            <h4 className="font-mono uppercase tracking-telemetry text-xs font-semibold text-fg-primary">
              {host.name}
            </h4>
            <span className={`font-mono text-[8px] uppercase px-1.5 py-0.5 rounded border ${
              isHermes ? "border-fg-primary text-fg-primary" : "border-edge text-fg-muted"
            }`}>
              {isHermes ? "Hermes" : "Manual"}
            </span>
          </div>
          <p className="font-mono text-[10px] text-fg-muted">{host.base_url}</p>
          {isHermes && host.provider_name && (
            <p className="font-mono text-[9px] text-fg-secondary mt-0.5">
              {host.profile_name || "default"}:{host.provider_name}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`font-mono text-[9px] uppercase ${statusColor}`}>
            {host.last_test_status || "unknown"}
          </span>
          <Toggle checked={host.enabled} onChange={() => onEdit(host, { enabled: !host.enabled })} />
        </div>
      </div>

      <div className="flex items-center gap-2 mb-2">
        <Button onClick={handleTest} variant="secondary" size="sm" disabled={testing}>
          {testing ? "Testing..." : "Test"}
        </Button>
        {!isHermes && (
          <Button onClick={handleRefresh} variant="secondary" size="sm" disabled={refreshing}>
            {refreshing ? "Refreshing..." : "Refresh Models"}
          </Button>
        )}
        <Button onClick={() => onEdit(host)} variant="secondary" size="sm">
          Edit
        </Button>
        {!isHermes && (
          <Button onClick={() => onDelete(host.id)} variant="danger" size="sm">
            Delete
          </Button>
        )}
      </div>

      {testResult && (
        <div className={`p-2 rounded text-[10px] font-mono ${testResult.success ? "bg-green-500/10 text-green-400" : "bg-red-500/10 text-red-400"}`}>
          {testResult.success ? "✓ Connection successful" : `✗ ${testResult.error}`}
          {testResult.latency_ms && <span> ({testResult.latency_ms}ms)</span>}
        </div>
      )}

      <div className="mt-2 flex items-center gap-4 text-[9px] font-mono text-fg-muted">
        {host.last_models_refresh_at && (
          <span>Models: {new Date(host.last_models_refresh_at).toLocaleString()}</span>
        )}
        {isHermes && host.last_synced_at && (
          <span>Synced: {new Date(host.last_synced_at).toLocaleString()}</span>
        )}
        {isHermes && host.last_sync_status && (
          <span className={host.last_sync_status === "success" ? "text-green-400" : host.last_sync_status === "stale" ? "text-amber-400" : "text-red-400"}>
            {host.last_sync_status}
          </span>
        )}
      </div>
    </div>
  );
}

export default function ModelHosts({ onBack }) {
  const [hosts, setHosts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [lastSync, setLastSync] = useState(null);

  const [formData, setFormData] = useState({
    name: "",
    provider: "openai_compatible",
    base_url: "",
    api_key: "",
    enabled: true,
    allow_cloud_endpoints: false,
  });

  useEffect(() => {
    loadHosts();
  }, []);

  async function loadHosts() {
    try {
      const data = await getJson("/settings/model-hosts");
      setHosts(data);
      setLoading(false);
    } catch (err) {
      setError(`Failed to load hosts: ${err.message}`);
      setLoading(false);
    }
  }

  async function handleSync() {
    setSyncing(true);
    setError(null);
    try {
      const result = await postJson("/settings/model-hosts/sync-hermes");
      setLastSync(result);
      loadHosts();
    } catch (err) {
      setError(`Sync failed: ${err.message}`);
    } finally {
      setSyncing(false);
    }
  }

  async function handleSave() {
    setError(null);
    try {
      if (editing) {
        await putJson(`/settings/model-hosts/${editing.id}`, formData);
      } else {
        await postJson("/settings/model-hosts", formData);
      }
      setShowForm(false);
      setEditing(null);
      setFormData({ name: "", provider: "openai_compatible", base_url: "", api_key: "", enabled: true, allow_cloud_endpoints: false });
      loadHosts();
    } catch (err) {
      setError(`Failed to save: ${err.message}`);
    }
  }

  function handleEdit(host, updates = null) {
    if (updates) {
      // Quick toggle update
      putJson(`/settings/model-hosts/${host.id}`, updates).then(loadHosts);
    } else {
      setEditing(host);
      setFormData({
        name: host.name,
        provider: host.provider,
        base_url: host.base_url,
        api_key: "",
        enabled: host.enabled,
        allow_cloud_endpoints: host.allow_cloud_endpoints,
      });
      setShowForm(true);
    }
  }

  async function handleDelete(id) {
    if (!confirm("Delete this model host?")) return;
    try {
      await fetch(`/api/settings/model-hosts/${id}`, { method: "DELETE" });
      loadHosts();
    } catch (err) {
      setError(`Failed to delete: ${err.message}`);
    }
  }

  function handleRefresh(hostId) {
    loadHosts();
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="font-mono text-xs text-fg-secondary">Loading hosts...</span>
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="font-display font-extrabold tracking-tighter-display text-fg-primary text-2xl mb-1">
            MODEL PROVIDERS
          </h1>
          <p className="font-mono text-xs text-fg-muted">
            Model hosts are AI/model endpoints used by Debate Execution. Sync from Hermes or add manually.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button onClick={handleSync} variant="primary" disabled={syncing}>
            {syncing ? "Syncing..." : "Sync from Hermes"}
          </Button>
          <Button onClick={() => { setShowForm(true); setEditing(null); setFormData({ name: "", provider: "openai_compatible", base_url: "", api_key: "", enabled: true, allow_cloud_endpoints: false }); }}>
            Add Host
          </Button>
        </div>
      </div>

      {lastSync && (
        <div className={`mb-4 border rounded p-3 ${lastSync.error_message ? "border-amber-500 bg-amber-500/10" : "border-green-500 bg-green-500/10"}`}>
          <p className={`font-mono text-xs ${lastSync.error_message ? "text-amber-400" : "text-green-400"}`}>
            {lastSync.error_message 
              ? `⚠ ${lastSync.error_message}`
              : `✓ Synced ${lastSync.hosts_discovered} hosts, ${lastSync.models_discovered} models from Hermes`}
          </p>
        </div>
      )}

      {error && (
        <div className="mb-4 border border-red-500 bg-red-500/10 rounded p-3">
          <p className="font-mono text-xs text-red-400">{error}</p>
        </div>
      )}

      {showForm && (
        <Section title={editing ? "Edit Model Host" : "Add Model Host"}>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Display Name">
              <TextInput
                value={formData.name}
                onChange={(v) => setFormData({ ...formData, name: v })}
                placeholder="e.g., ai-4080"
              />
            </Field>

            <Field label="Provider">
              <SelectInput
                value={formData.provider}
                onChange={(v) => setFormData({ ...formData, provider: v })}
                options={[
                  { value: "openai_compatible", label: "OpenAI Compatible" },
                ]}
              />
            </Field>

            <Field label="Base URL" hint="http:// or https:// only">
              <TextInput
                value={formData.base_url}
                onChange={(v) => setFormData({ ...formData, base_url: v })}
                placeholder="http://ai-4080:11434/v1"
              />
            </Field>

            <Field label="API Key" hint="Optional for local models">
              <TextInput
                type="password"
                value={formData.api_key}
                onChange={(v) => setFormData({ ...formData, api_key: v })}
                placeholder="Enter API key"
              />
            </Field>
          </div>

          <div className="mt-4 flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Toggle
                checked={formData.enabled}
                onChange={(v) => setFormData({ ...formData, enabled: v })}
              />
              <span className="font-mono text-xs text-fg-secondary">Enabled</span>
            </div>

            <div className="flex items-center gap-2">
              <Toggle
                checked={formData.allow_cloud_endpoints}
                onChange={(v) => setFormData({ ...formData, allow_cloud_endpoints: v })}
              />
              <span className="font-mono text-xs text-fg-secondary">Allow Cloud Endpoints</span>
            </div>
          </div>

          <div className="mt-4 flex gap-2">
            <Button onClick={handleSave}>{editing ? "Update" : "Add"} Host</Button>
            <Button onClick={() => { setShowForm(false); setEditing(null); }} variant="secondary">Cancel</Button>
          </div>
        </Section>
      )}

      <Section title="Configured Hosts">
        {hosts.length === 0 ? (
          <p className="font-mono text-xs text-fg-muted">No model hosts configured. Click "Add Host" to create one.</p>
        ) : (
          hosts.map((host) => (
            <HostCard
              key={host.id}
              host={host}
              onEdit={handleEdit}
              onRefresh={handleRefresh}
              onDelete={handleDelete}
            />
          ))
        )}
      </Section>

      <div className="mt-4">
        <Button onClick={onBack} variant="secondary">Back to Settings</Button>
      </div>
    </div>
  );
}
