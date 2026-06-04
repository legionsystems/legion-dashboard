import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getJson, putJson, postJson } from "../api/client.js";

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

function SelectInput({ value, onChange, options, disabled, placeholder = "-- Select --" }) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className="w-full bg-canvas border border-edge rounded px-2 py-1.5 font-mono text-xs text-fg-primary focus:outline-none focus:border-fg-primary"
    >
      <option value="">{placeholder}</option>
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
  const sizes = { sm: "px-2 py-1 text-[9px]", md: "px-4 py-2" };
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

function ModelSelector({ label, hostId, modelId, hosts, modelsByHost, onHostChange, onModelChange, onRefresh, hint }) {
  const models = modelsByHost[hostId] || [];
  
  return (
    <div className="border border-edge bg-canvas rounded p-3 mb-3">
      <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-secondary mb-2">{label}</p>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="block font-mono text-[9px] text-fg-muted mb-1">Host</label>
          <SelectInput
            value={hostId || ""}
            onChange={onHostChange}
            options={hosts.map(h => ({ value: String(h.id), label: h.name }))}
            placeholder="-- Select Host --"
          />
        </div>
        <div>
          <label className="block font-mono text-[9px] text-fg-muted mb-1">Model</label>
          <div className="flex gap-1">
            <SelectInput
              value={modelId || ""}
              onChange={onModelChange}
              options={models.map(m => ({ value: m.model_id, label: m.display_name || m.model_id }))}
              disabled={!hostId}
              placeholder={hostId ? "-- Select Model --" : "Select host first"}
            />
            {hostId && (
              <Button onClick={() => onRefresh(hostId)} size="sm" variant="secondary">↻</Button>
            )}
          </div>
        </div>
      </div>
      {hint && <p className="font-mono text-[9px] text-fg-muted mt-1">{hint}</p>}
      {hostId && models.length === 0 && (
        <p className="font-mono text-[9px] text-amber-400 mt-1">No models loaded. Click ↻ to refresh.</p>
      )}
    </div>
  );
}

export default function Settings() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState(null);
  
  const [hosts, setHosts] = useState([]);
  const [modelsByHost, setModelsByHost] = useState({});

  const [config, setConfig] = useState({
    enabled: false,
    provider: "openai_compatible",
    base_url: "",
    model_mode: "single_model",
    default_host_id: null,
    default_model: "",
    pro_host_id: null,
    pro_model: "",
    con_host_id: null,
    con_model: "",
    arbiter_host_id: null,
    arbiter_model: "",
    fallback_host_id: null,
    fallback_model: "",
    api_key: "",
    clear_api_key: false,
    timeout_seconds: 180,
    max_output_chars: 12000,
    default_rounds: 2,
    allow_cloud_endpoints: false,
    notes: "",
    // Warmup settings
    warm_model_before_debate: true,
    warmup_timeout_seconds: 300,
    keep_model_loaded_for: "1h",
    fail_debate_if_warmup_fails: true,
    // Failed run display
    visible_failed_runs_limit: 2,
    // Worker configuration
    execution_backend: "worker",
    worker_enabled: true,
    worker_poll_interval_seconds: 3,
    worker_lease_seconds: 300,
    worker_heartbeat_seconds: 10,
    worker_max_concurrent_runs: 1,
    turn_timeout_seconds: null,
    whole_run_timeout_seconds: null,
    retry_failed_turn_enabled: true,
    max_turn_retries: 1,
  });

  useEffect(() => {
    loadAll();
  }, []);

  async function loadAll() {
    try {
      const [hostsData, configData] = await Promise.all([
        getJson("/settings/model-hosts"),
        getJson("/settings/debate-execution"),
      ]);
      // Filter to enabled hosts only for normal dropdown
      const enabledHosts = hostsData.filter(h => h.enabled);
      setHosts(enabledHosts);
      // Persisted config values are authoritative - load them into state
      setConfig((prev) => ({
        ...prev,
        ...configData,
        api_key: "",
        clear_api_key: false,
      }));
      // Preload models for any selected hosts
      if (configData.default_host_id) {
        loadModels(String(configData.default_host_id));
      }
      if (configData.pro_host_id) {
        loadModels(String(configData.pro_host_id));
      }
      if (configData.con_host_id) {
        loadModels(String(configData.con_host_id));
      }
      if (configData.arbiter_host_id) {
        loadModels(String(configData.arbiter_host_id));
      }
      setLoading(false);
    } catch (err) {
      setError(`Failed to load settings: ${err.message}`);
      setLoading(false);
    }
  }

  async function loadModels(hostId) {
    try {
      const models = await getJson(`/settings/model-hosts/${hostId}/models`);
      setModelsByHost((prev) => ({ ...prev, [hostId]: models }));
    } catch (err) {
      // Silent fail - UI shows "no models"
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const payload = { ...config };
      if (!payload.api_key) delete payload.api_key;
      await putJson("/settings/debate-execution", payload);
      setConfig((prev) => ({ ...prev, api_key: "", clear_api_key: false }));
      setTestResult(null);
    } catch (err) {
      setError(`Failed to save: ${err.message}`);
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    setError(null);
    try {
      const payload = {};
      if (config.base_url) payload.base_url = config.base_url;
      if (config.default_model) payload.model = config.default_model;
      if (config.timeout_seconds) payload.timeout_seconds = config.timeout_seconds;
      const result = await postJson("/settings/debate-execution/test", payload);
      setTestResult(result);
    } catch (err) {
      setError(`Test failed: ${err.message}`);
    } finally {
      setTesting(false);
    }
  }

  function updateField(field, value) {
    setConfig((prev) => ({ ...prev, [field]: value }));
  }

  function handleHostChange(fieldPrefix, hostId) {
    updateField(`${fieldPrefix}_host_id`, hostId ? parseInt(hostId) : null);
    updateField(`${fieldPrefix}_model`, "");
    if (hostId) loadModels(hostId);
  }

  function handleModelChange(fieldPrefix, modelId) {
    updateField(`${fieldPrefix}_model`, modelId);
  }

  function handleRefresh(hostId) {
    postJson(`/settings/model-hosts/${hostId}/refresh-models`).then(() => loadModels(hostId));
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="font-mono text-xs text-fg-secondary">Loading settings...</span>
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="font-display font-extrabold tracking-tighter-display text-fg-primary text-2xl mb-1">
            SETTINGS
          </h1>
          <p className="font-mono text-xs text-fg-muted">
            Configure debate execution and model providers
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => navigate("/settings/executor-allowlist")} variant="secondary" size="sm">
            Executor Allowlist
          </Button>
          <Button onClick={() => navigate("/settings/model-providers")} variant="secondary" size="sm">
            Manage Model Providers
          </Button>
        </div>
      </div>

      {error && (
        <div className="mb-4 border border-red-500 bg-red-500/10 rounded p-3">
          <p className="font-mono text-xs text-red-400">{error}</p>
        </div>
      )}

      <Section title="Debate Execution">
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div className="flex items-center justify-between p-3 border border-edge rounded bg-canvas">
            <div>
              <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-primary">Enabled</p>
              <p className="font-mono text-[10px] text-fg-muted mt-0.5">Allow debate execution bridge</p>
            </div>
            <Toggle checked={config.enabled} onChange={(v) => updateField("enabled", v)} />
          </div>
          <div className="flex items-center justify-between p-3 border border-edge rounded bg-canvas">
            <div>
              <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-primary">Allow Cloud Endpoints</p>
              <p className="font-mono text-[10px] text-fg-muted mt-0.5">Permit public/cloud model providers</p>
            </div>
            <Toggle checked={config.allow_cloud_endpoints} onChange={(v) => updateField("allow_cloud_endpoints", v)} />
          </div>
        </div>

        <Field label="Model Mode">
          <SelectInput
            value={config.model_mode}
            onChange={(v) => updateField("model_mode", v)}
            options={[
              { value: "single_model", label: "Use one model for all roles (recommended)" },
              { value: "role_models", label: "Use separate models per role (advanced)" },
            ]}
          />
        </Field>

        {config.model_mode === "single_model" ? (
          <ModelSelector
            label="Default Model (all roles)"
            hostId={config.default_host_id}
            modelId={config.default_model}
            hosts={hosts}
            modelsByHost={modelsByHost}
            onHostChange={(v) => handleHostChange("default", v)}
            onModelChange={(v) => handleModelChange("default", v)}
            onRefresh={handleRefresh}
            hint="This model will be used for all debate roles"
          />
        ) : (
          <div>
            <ModelSelector
              label="Pro/Builder Model"
              hostId={config.pro_host_id}
              modelId={config.pro_model}
              hosts={hosts}
              modelsByHost={modelsByHost}
              onHostChange={(v) => handleHostChange("pro", v)}
              onModelChange={(v) => handleModelChange("pro", v)}
              onRefresh={handleRefresh}
              hint="Product Owner, UX/Design Reviewer, Technical Architect, Builder"
            />
            <ModelSelector
              label="Con/Skeptic Model"
              hostId={config.con_host_id}
              modelId={config.con_model}
              hosts={hosts}
              modelsByHost={modelsByHost}
              onHostChange={(v) => handleHostChange("con", v)}
              onModelChange={(v) => handleModelChange("con", v)}
              onRefresh={handleRefresh}
              hint="Skeptic/Red Team, Security/Privacy Reviewer"
            />
            <ModelSelector
              label="Arbiter Model"
              hostId={config.arbiter_host_id}
              modelId={config.arbiter_model}
              hosts={hosts}
              modelsByHost={modelsByHost}
              onHostChange={(v) => handleHostChange("arbiter", v)}
              onModelChange={(v) => handleModelChange("arbiter", v)}
              onRefresh={handleRefresh}
              hint="Final Arbiter"
            />
          </div>
        )}

        <div className="grid grid-cols-3 gap-4 mt-4">
          <Field label="Timeout (seconds)">
            <TextInput type="number" value={config.timeout_seconds} onChange={(v) => updateField("timeout_seconds", parseInt(v) || 0)} />
          </Field>
          <Field label="Max Output Chars">
            <TextInput type="number" value={config.max_output_chars} onChange={(v) => updateField("max_output_chars", parseInt(v) || 0)} />
          </Field>
          <Field label="Default Rounds" hint="1-5">
            <TextInput type="number" value={config.default_rounds} onChange={(v) => updateField("default_rounds", parseInt(v) || 0)} />
          </Field>
        </div>

        <div className="border border-edge bg-canvas rounded p-3 mt-4">
          <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-primary mb-2">Model Warmup</p>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <div className="flex items-center justify-between p-2 border border-edge rounded">
              <div>
                <p className="font-mono text-[10px] text-fg-primary">Warm model before debate</p>
                <p className="font-mono text-[9px] text-fg-muted mt-0.5">Load model first, then start debate timer</p>
              </div>
              <Toggle checked={config.warm_model_before_debate} onChange={(v) => updateField("warm_model_before_debate", v)} />
            </div>
            <div className="flex items-center justify-between p-2 border border-edge rounded">
              <div>
                <p className="font-mono text-[10px] text-fg-primary">Fail if warmup fails</p>
                <p className="font-mono text-[9px] text-fg-muted mt-0.5">Stop debate if model won't load</p>
              </div>
              <Toggle checked={config.fail_debate_if_warmup_fails} onChange={(v) => updateField("fail_debate_if_warmup_fails", v)} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Warmup Timeout (seconds)" hint="Max time to wait for model load">
              <TextInput type="number" value={config.warmup_timeout_seconds} onChange={(v) => updateField("warmup_timeout_seconds", parseInt(v) || 0)} />
            </Field>
            <Field label="Keep Model Loaded For" hint="How long to keep model resident">
              <SelectInput
                value={config.keep_model_loaded_for}
                onChange={(v) => updateField("keep_model_loaded_for", v)}
                options={[
                  { value: "5m", label: "5 minutes" },
                  { value: "30m", label: "30 minutes" },
                  { value: "1h", label: "1 hour" },
                  { value: "4h", label: "4 hours" },
                ]}
              />
            </Field>
          </div>
        </div>

        <div className="border border-edge bg-canvas rounded p-3 mt-4">
          <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-primary mb-2">Failed Run Display</p>
          <Field label="Visible failed debate attempts" hint="Show latest N failures, collapse older ones">
            <SelectInput
              value={config.visible_failed_runs_limit}
              onChange={(v) => updateField("visible_failed_runs_limit", parseInt(v) || 2)}
              options={[
                { value: "1", label: "1 (show latest only)" },
                { value: "2", label: "2 (recommended)" },
                { value: "3", label: "3" },
              ]}
            />
          </Field>
        </div>

        <div className="border border-edge bg-canvas rounded p-3 mt-4">
          <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-primary mb-2">Display Settings</p>
          <Field label="Display Timezone" hint="IANA timezone name for timestamp display">
            <TextInput
              value={config.display_timezone || "Australia/Sydney"}
              onChange={(v) => updateField("display_timezone", v)}
              placeholder="Australia/Sydney"
            />
          </Field>
        </div>

        <div className="border border-edge bg-canvas rounded p-3 mt-4">
          <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-primary mb-2">Worker Execution</p>
          <p className="font-mono text-[9px] text-fg-muted mb-3">Worker mode queues debates and processes them in the background. The browser can be closed while the worker continues. Partial turns are saved as they complete.</p>
          <div className="grid grid-cols-2 gap-3 mb-3">
            <div className="flex items-center justify-between p-2 border border-edge rounded">
              <div>
                <p className="font-mono text-[10px] text-fg-primary">Worker Enabled</p>
                <p className="font-mono text-[9px] text-fg-muted mt-0.5">Process debates in background</p>
              </div>
              <Toggle checked={config.worker_enabled} onChange={(v) => updateField("worker_enabled", v)} />
            </div>
            <div className="flex items-center justify-between p-2 border border-edge rounded">
              <div>
                <p className="font-mono text-[10px] text-fg-primary">Retry Failed Turn</p>
                <p className="font-mono text-[9px] text-fg-muted mt-0.5">Auto-retry failed turns</p>
              </div>
              <Toggle checked={config.retry_failed_turn_enabled} onChange={(v) => updateField("retry_failed_turn_enabled", v)} />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Poll Interval (seconds)" hint="How often worker checks for jobs">
              <TextInput type="number" value={config.worker_poll_interval_seconds} onChange={(v) => updateField("worker_poll_interval_seconds", parseInt(v) || 3)} />
            </Field>
            <Field label="Lease Duration (seconds)" hint="Job lock timeout">
              <TextInput type="number" value={config.worker_lease_seconds} onChange={(v) => updateField("worker_lease_seconds", parseInt(v) || 300)} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3 mt-3">
            <Field label="Per-Turn Timeout (seconds)" hint="Max time per debate turn">
              <TextInput type="number" value={config.turn_timeout_seconds || ""} onChange={(v) => updateField("turn_timeout_seconds", parseInt(v) || null)} placeholder="Uses generation timeout" />
            </Field>
            <Field label="Max Turn Retries" hint="Retry attempts per turn">
              <TextInput type="number" value={config.max_turn_retries} onChange={(v) => updateField("max_turn_retries", parseInt(v) || 1)} />
            </Field>
          </div>
        </div>

        <Field label="API Key" hint={config.api_key_configured ? "Currently configured — enter new value to replace, or check clear" : "Optional for local models"}>
          <TextInput type="password" value={config.api_key} onChange={(v) => updateField("api_key", v)} placeholder={config.api_key_configured ? "Enter new key to replace" : "Enter API key (optional)"} />
          {config.api_key_configured && (
            <div className="mt-2 flex items-center gap-2">
              <input type="checkbox" id="clear-api-key" checked={config.clear_api_key} onChange={(e) => updateField("clear_api_key", e.target.checked)} className="rounded border-edge bg-canvas" />
              <label htmlFor="clear-api-key" className="font-mono text-[10px] text-fg-secondary">Clear stored API key</label>
            </div>
          )}
        </Field>

        {testResult && (
          <div className={`mt-4 p-3 rounded border ${testResult.success ? "border-green-500 bg-green-500/10" : "border-red-500 bg-red-500/10"}`}>
            <p className={`font-mono text-xs ${testResult.success ? "text-green-400" : "text-red-400"}`}>
              {testResult.success ? "✓ Connection successful" : "✗ Connection failed"}
            </p>
            {testResult.latency_ms && <p className="font-mono text-[10px] text-fg-muted mt-1">Latency: {testResult.latency_ms}ms</p>}
            {testResult.error && <p className="font-mono text-[10px] text-fg-muted mt-1">{testResult.error}</p>}
          </div>
        )}

        <div className="mt-4 flex gap-2">
          <Button onClick={handleSave} disabled={saving}>{saving ? "Saving..." : "Save Settings"}</Button>
          <Button onClick={handleTest} variant="secondary" disabled={testing}>{testing ? "Testing..." : "Test Connection"}</Button>
          <Button onClick={() => navigate("/work-items")} variant="secondary">Back to Work Items</Button>
        </div>
      </Section>
    </div>
  );
}
