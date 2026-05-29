import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getJson, postJson } from "../api/client.js";
import {
  ALL_PRIORITIES,
  ALL_SOURCES,
  INTAKE_TYPES,
  PriorityBadge,
  TypeBadge,
  priorityMeta,
  sourceMeta,
  typeMeta,
} from "./badges.jsx";
import { Panel } from "./panel.jsx";
import { Button } from "./buttons.jsx";
import { ErrorBanner, SkeletonBlock } from "./states.jsx";

const TYPE_COPY = {
  idea: {
    sector: "INTAKE / IDEA",
    headline: "NEW IDEA",
    titleHint: "// what is the idea, in one line?",
    bodyHint: "// problem, hypothesis, sketch",
    cta: "+ FILE IDEA",
  },
  bug: {
    sector: "INTAKE / BUG",
    headline: "NEW BUG",
    titleHint: "// symptom: what's broken?",
    bodyHint: "// steps to reproduce, expected vs actual",
    cta: "+ FILE BUG",
  },
  change: {
    sector: "INTAKE / CHANGE",
    headline: "NEW CHANGE",
    titleHint: "// what is being changed?",
    bodyHint: "// scope, motivation, blast radius",
    cta: "+ FILE CHANGE",
  },
  task: {
    sector: "INTAKE / TASK",
    headline: "NEW TASK",
    titleHint: "// short, actionable summary",
    bodyHint: "// concrete steps to complete",
    cta: "+ FILE TASK",
  },
  slice: {
    sector: "INTAKE / SLICE",
    headline: "NEW SLICE",
    titleHint: "// vertical slice of value",
    bodyHint: "// user-facing outcome + the smallest cut to deliver it",
    cta: "+ FILE SLICE",
  },
};

const INPUT_BASE =
  "w-full px-3 py-2 text-sm font-mono bg-canvas border border-edge-strong focus:border-fg-primary outline-none transition-colors";

function Field({ label, hint, error, required, children }) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="label-tel-strong">
          {label}
          {required && <span className="text-alert ml-1">*</span>}
        </span>
        {hint && <span className="label-tel">{hint}</span>}
      </div>
      {children}
      {error && (
        <div className="mt-1 font-mono text-[11px] tracking-telemetry text-alert">
          ! {error}
        </div>
      )}
    </label>
  );
}

function TypePicker({ value, onChange }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {INTAKE_TYPES.map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => onChange(t)}
          className={`transition-opacity ${
            value === t ? "opacity-100" : "opacity-40 hover:opacity-70"
          }`}
        >
          <TypeBadge type={t} />
        </button>
      ))}
    </div>
  );
}

function PriorityPicker({ value, onChange }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {ALL_PRIORITIES.map((p) => (
        <button
          key={p}
          type="button"
          onClick={() => onChange(p)}
          className={`transition-opacity ${
            value === p ? "opacity-100" : "opacity-40 hover:opacity-70"
          }`}
        >
          <PriorityBadge priority={p} />
        </button>
      ))}
    </div>
  );
}

export default function WorkIntakeForm() {
  const { type: typeParam } = useParams();
  const initialType = INTAKE_TYPES.includes(typeParam) ? typeParam : "task";
  const navigate = useNavigate();

  const [form, setForm] = useState({
    type: initialType,
    title: "",
    body: "",
    target_app: "",
    priority: "medium",
    tags: "",
    source: "operator",
    acceptance_notes: "",
  });
  const [apps, setApps] = useState(null);
  const [appsError, setAppsError] = useState(null);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [touched, setTouched] = useState({});

  useEffect(() => {
    setForm((prev) => ({ ...prev, type: initialType }));
  }, [initialType]);

  useEffect(() => {
    let cancelled = false;
    getJson("/apps")
      .then((data) => {
        if (!cancelled) setApps(data);
      })
      .catch((err) => {
        if (!cancelled) setAppsError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function blur(field) {
    setTouched((prev) => ({ ...prev, [field]: true }));
  }

  const titleError =
    (touched.title || submitting) && !form.title.trim()
      ? "Title is required"
      : null;

  const copy = TYPE_COPY[form.type] || TYPE_COPY.task;

  function submit(event) {
    event.preventDefault();
    setTouched({ title: true });
    if (!form.title.trim()) return;
    setSubmitting(true);
    setError(null);
    const payload = {
      ...form,
      status: "draft",
      target_app: form.target_app || null,
      tags: form.tags.trim() || null,
      acceptance_notes: form.acceptance_notes.trim() || null,
    };
    postJson("/work-items", payload)
      .then((data) => navigate(`/work-items/${data.id}`))
      .catch((err) => setError(err.message))
      .finally(() => setSubmitting(false));
  }

  return (
    <div className="space-y-5 max-w-3xl">
      <div>
        <Link
          to="/work-items/new"
          className="font-mono uppercase tracking-telemetry text-[11px] text-fg-secondary hover:text-fg-primary"
        >
          ← CHANGE TYPE
        </Link>
      </div>

      <div>
        <div className="label-tel">{copy.sector}</div>
        <h1
          className="font-display font-extrabold tracking-tighter-display leading-none mt-1"
          style={{ fontSize: "clamp(2rem, 5vw, 3rem)" }}
        >
          {copy.headline}
        </h1>
      </div>

      <ErrorBanner message={error} />

      <form onSubmit={submit} className="space-y-5">
        <Panel title="TYPE / PRIORITY" subtitle="// classification">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            <Field label="TYPE" required>
              <TypePicker
                value={form.type}
                onChange={(t) => update("type", t)}
              />
            </Field>
            <Field label="PRIORITY" required>
              <PriorityPicker
                value={form.priority}
                onChange={(p) => update("priority", p)}
              />
            </Field>
          </div>
        </Panel>

        <Panel title={copy.headline} subtitle="// payload">
          <div className="space-y-5">
            <Field
              label="TITLE"
              hint={`${form.title.length} CHARS`}
              error={titleError}
              required
            >
              <input
                type="text"
                value={form.title}
                onChange={(e) => update("title", e.target.value)}
                onBlur={() => blur("title")}
                placeholder={copy.titleHint}
                className={`${INPUT_BASE} ${titleError ? "border-alert" : ""}`}
              />
            </Field>

            <Field
              label="DESCRIPTION"
              hint={`${form.body.length} CHARS // OPTIONAL`}
            >
              <textarea
                value={form.body}
                onChange={(e) => update("body", e.target.value)}
                rows={6}
                placeholder={copy.bodyHint}
                className={INPUT_BASE}
              />
            </Field>

            <Field
              label="ACCEPTANCE NOTES"
              hint="// OPTIONAL — definition of done"
            >
              <textarea
                value={form.acceptance_notes}
                onChange={(e) =>
                  update("acceptance_notes", e.target.value)
                }
                rows={3}
                placeholder="// criteria the work must satisfy"
                className={INPUT_BASE}
              />
            </Field>
          </div>
        </Panel>

        <Panel title="CONTEXT" subtitle="// routing">
          <div className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              <Field
                label="TARGET APP"
                hint={appsError ? "// APP LIST UNAVAILABLE" : "// OPTIONAL"}
              >
                <select
                  value={form.target_app}
                  onChange={(e) => update("target_app", e.target.value)}
                  className={INPUT_BASE}
                  disabled={apps === null && !appsError}
                >
                  <option value="">— UNASSIGNED —</option>
                  {apps?.map((a) => (
                    <option key={a.app_id} value={a.app_id}>
                      {a.app_id}
                    </option>
                  ))}
                </select>
                {apps === null && !appsError && (
                  <div className="mt-1 label-tel">LOADING APPS…</div>
                )}
              </Field>

              <Field label="SOURCE" required>
                <select
                  value={form.source}
                  onChange={(e) => update("source", e.target.value)}
                  className={INPUT_BASE}
                >
                  {ALL_SOURCES.map((s) => (
                    <option key={s} value={s}>
                      {sourceMeta(s).label}
                    </option>
                  ))}
                </select>
              </Field>
            </div>

            <Field
              label="TAGS"
              hint="// COMMA-SEPARATED, OPTIONAL"
            >
              <input
                type="text"
                value={form.tags}
                onChange={(e) => update("tags", e.target.value)}
                placeholder="// e.g. ux, perf, intake"
                className={INPUT_BASE}
              />
            </Field>
          </div>
        </Panel>

        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border border-edge bg-surface px-4 py-3">
          <div className="label-tel">
            NEW RECORD // DEFAULT STATUS = DRAFT
          </div>
          <div className="flex gap-2">
            <Link to="/work-items">
              <Button variant="ghost" type="button">CANCEL</Button>
            </Link>
            <Button
              variant="primary"
              type="submit"
              disabled={submitting || !!titleError}
            >
              {submitting ? "WRITING…" : copy.cta}
            </Button>
          </div>
        </div>
      </form>
    </div>
  );
}
