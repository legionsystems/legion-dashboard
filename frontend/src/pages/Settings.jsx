import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getJson, putJson, postJson } from "../api/client.js";

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

function Button({ children, onClick, variant = "primary", disabled, type = "button" }) {
  const base = "font-mono uppercase tracking-telemetry text-[10px] font-semibold px-4 py-2 rounded border transition-colors";
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
      className={`${base} ${variants[variant]} ${disabled ? "opacity-50 cursor-not-allowed" : ""}`}
    >
      {children}
    </button>
  );
}

export default function Settings() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null);
  const [error, setError] = useState(null);

  const [config, setConfig] = useState({
    enabled: false,
    provider: "openai_compatible",
    base_url: "",
    model_mode: "single_model",
    default_model: "",
    pro_model: "",
    con_model: "",
    arbiter_model: "",
    fallback_model: "",
    api_key: "",
    clear_api_key: false,
    timeout_seconds: 180,
    max_output_chars: 12000,
    default_rounds: 2,
    allow_cloud_endpoints: false,
    notes: "",
  });

  useEffect(() => {
    loadConfig();
  }, []);

  async function loadConfig() {
    try {
      const data = await getJson("/settings/debate-execution");
      setConfig((prev) => ({
        ...prev,
        ...data,
        api_key: "", // Never load API key into form
        clear_api_key: false,
      }));
      setLoading(false);
    } catch (err) {
      setError(`Failed to load settings: ${err.message}`);
      setLoading(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      const payload = { ...config };
      // Don't send empty API key
      if (!payload.api_key) {
        delete payload.api_key;
      }
      await putJson("/settings/debate-execution", payload);
      // Clear API key field after save (never display it)
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

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="font-mono text-xs text-fg-secondary">Loading settings...</span>
      </div>
    );
  }

  return (
    <div className="max-w-4xl">
      <div className="mb-6">
        <h1 className="font-display font-extrabold tracking-tighter-display text-fg-primary text-2xl mb-1">
          SETTINGS
        </h1>
        <p className="font-mono text-xs text-fg-muted">
          Configure debate execution and other system settings
        </p>
      </div>

      {error && (
        <div className="mb-4 border border-red-500 bg-red-500/10 rounded p-3">
          <p className="font-mono text-xs text-red-400">{error}</p>
        </div>
      )}

      <Section title="Debate Execution">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="flex items-center justify-between p-3 border border-edge rounded bg-canvas">
            <div>
              <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-primary">
                Enabled
              </p>
              <p className="font-mono text-[10px] text-fg-muted mt-0.5">
                Allow debate execution bridge
              </p>
            </div>
            <Toggle
              checked={config.enabled}
              onChange={(v) => updateField("enabled", v)}
            />
          </div>

          <div className="flex items-center justify-between p-3 border border-edge rounded bg-canvas">
            <div>
              <p className="font-mono uppercase tracking-telemetry text-[10px] text-fg-primary">
                Allow Cloud Endpoints
              </p>
              <p className="font-mono text-[10px] text-fg-muted mt-0.5">
                Permit public/cloud model providers
              </p>
            </div>
            <Toggle
              checked={config.allow_cloud_endpoints}
              onChange={(v) => updateField("allow_cloud_endpoints", v)}
            />
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
          <Field label="Provider">
            <SelectInput
              value={config.provider}
              onChange={(v) => updateField("provider", v)}
              options={[
                { value: "openai_compatible", label: "OpenAI Compatible" },
              ]}
            />
          </Field>

          <Field label="Base URL" hint="Local endpoints always allowed">
            <TextInput
              value={config.base_url}
              onChange={(v) => updateField("base_url", v)}
              placeholder="http://ai-4080:11434/v1"
            />
          </Field>
        </div>

        <Field label="Model Mode">
          <SelectInput
            value={config.model_mode}
            onChange={(v) => updateField("model_mode", v)}
            options={[
              { value: "single_model", label: "Use one model for all roles" },
              { value: "role_models", label: "Use separate models per role (advanced)" },
            ]}
          />
        </Field>

        <Field label="Default Model" hint="Used in single_model mode, fallback for role_models">
          <TextInput
            value={config.default_model}
            onChange={(v) => updateField("default_model", v)}
            placeholder="deepseek-r1:32b"
          />
        </Field>

        {config.model_mode === "role_models" && (
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4">
            <Field label="Pro/Builder Model" hint="Product Owner, UX, Architect, Builder">
              <TextInput
                value={config.pro_model}
                onChange={(v) => updateField("pro_model", v)}
                placeholder="Uses default_model if empty"
              />
            </Field>

            <Field label="Con/Skeptic Model" hint="Skeptic, Red Team, Security">
              <TextInput
                value={config.con_model}
                onChange={(v) => updateField("con_model", v)}
                placeholder="Uses default_model if empty"
              />
            </Field>

            <Field label="Arbiter Model" hint="Final Arbiter">
              <TextInput
                value={config.arbiter_model}
                onChange={(v) => updateField("arbiter_model", v)}
                placeholder="Uses default_model if empty"
              />
            </Field>

            <Field label="Fallback Model" hint="Optional backup">
              <TextInput
                value={config.fallback_model}
                onChange={(v) => updateField("fallback_model", v)}
                placeholder="Optional"
              />
            </Field>
          </div>
        )}

        <div className="mt-4 grid grid-cols-1 md:grid-cols-3 gap-4">
          <Field label="Timeout (seconds)">
            <TextInput
              type="number"
              value={config.timeout_seconds}
              onChange={(v) => updateField("timeout_seconds", parseInt(v) || 0)}
            />
          </Field>

          <Field label="Max Output Chars">
            <TextInput
              type="number"
              value={config.max_output_chars}
              onChange={(v) => updateField("max_output_chars", parseInt(v) || 0)}
            />
          </Field>

          <Field label="Default Rounds" hint="1-5">
            <TextInput
              type="number"
              value={config.default_rounds}
              onChange={(v) => updateField("default_rounds", parseInt(v) || 0)}
            />
          </Field>
        </div>

        <Field
          label="API Key"
          hint={config.api_key_configured ? "Currently configured — enter new value to replace, or check clear" : "Optional for local models"}
        >
          <div className="flex gap-2">
            <TextInput
              type="password"
              value={config.api_key}
              onChange={(v) => updateField("api_key", v)}
              placeholder={config.api_key_configured ? "Enter new key to replace" : "Enter API key (optional)"}
            />
          </div>
          {config.api_key_configured && (
            <div className="mt-2 flex items-center gap-2">
              <input
                type="checkbox"
                id="clear-api-key"
                checked={config.clear_api_key}
                onChange={(e) => updateField("clear_api_key", e.target.checked)}
                className="rounded border-edge bg-canvas"
              />
              <label htmlFor="clear-api-key" className="font-mono text-[10px] text-fg-secondary">
                Clear stored API key
              </label>
            </div>
          )}
        </Field>

        <Field label="Notes" hint="Optional description">
          <TextInput
            value={config.notes}
            onChange={(v) => updateField("notes", v)}
            placeholder="Optional notes about this configuration"
          />
        </Field>

        {testResult && (
          <div className={`mt-4 p-3 rounded border ${testResult.success ? "border-green-500 bg-green-500/10" : "border-red-500 bg-red-500/10"}`}>
            <p className={`font-mono text-xs ${testResult.success ? "text-green-400" : "text-red-400"}`}>
              {testResult.success ? "✓ Connection successful" : "✗ Connection failed"}
            </p>
            {testResult.latency_ms && (
              <p className="font-mono text-[10px] text-fg-muted mt-1">
                Latency: {testResult.latency_ms}ms
              </p>
            )}
            {testResult.error && (
              <p className="font-mono text-[10px] text-fg-muted mt-1">
                {testResult.error}
              </p>
            )}
          </div>
        )}

        <div className="mt-4 flex gap-2">
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "Saving..." : "Save Settings"}
          </Button>
          <Button onClick={handleTest} variant="secondary" disabled={testing}>
            {testing ? "Testing..." : "Test Connection"}
          </Button>
          <Button onClick={() => navigate("/work-items")} variant="secondary">
            Back to Work Items
          </Button>
        </div>
      </Section>
    </div>
  );
}
