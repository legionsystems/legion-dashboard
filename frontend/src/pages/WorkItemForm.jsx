import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getJson, postJson, putJson, uploadFile } from "../api/client.js";
import {
  ALL_STATUSES,
  ALL_TYPES,
  StatusBadge,
  TypeBadge,
} from "../components/badges.jsx";
import { Panel } from "../components/panel.jsx";
import { Button } from "../components/buttons.jsx";
import { ErrorBanner, SkeletonBlock } from "../components/states.jsx";

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

export default function WorkItemForm() {
  const { id } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(id);

  const [form, setForm] = useState({
    type: "task",
    title: "",
    body: "",
    status: "draft",
  });
  const [loaded, setLoaded] = useState(!editing);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [touched, setTouched] = useState({});
  const [attachments, setAttachments] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);

  useEffect(() => {
    if (!editing) return;
    setLoaded(false);
    getJson(`/work-items/${id}`)
      .then((data) => {
        setForm({
          type: data.type,
          title: data.title,
          body: data.body || "",
          status: data.status,
        });
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoaded(true));
    getJson(`/work-items/${id}/attachments`)
      .then(setAttachments)
      .catch(() => undefined);
  }, [id, editing]);

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

  function submit(event) {
    event.preventDefault();
    setTouched({ title: true });
    if (!form.title.trim()) return;
    setSubmitting(true);
    setError(null);
    const promise = editing
      ? putJson(`/work-items/${id}`, form)
      : postJson("/work-items", form);
    promise
      .then((data) => navigate(`/work-items/${data.id}`))
      .catch((err) => setError(err.message))
      .finally(() => setSubmitting(false));
  }

  function handleFileSelect(event) {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    const file = files[0];
    setUploading(true);
    setUploadError(null);
    uploadFile(`/work-items/${id}/attachments`, file)
      .then((result) => {
        setAttachments((prev) => [result, ...prev]);
        event.target.value = "";
      })
      .catch((err) => {
        setUploadError(err.message);
      })
      .finally(() => setUploading(false));
  }

  const headerLabel = editing ? `EDIT // #${id}` : "NEW WORK ITEM";
  const inputBase =
    "w-full px-3 py-2 text-sm font-mono bg-canvas border border-edge-strong focus:border-fg-primary outline-none transition-colors";

  return (
    <div className="space-y-5 max-w-3xl">
      <div>
        <Link
          to={editing ? `/work-items/${id}` : "/work-items"}
          className="font-mono uppercase tracking-telemetry text-[11px] text-fg-secondary hover:text-fg-primary"
        >
          ← CANCEL & RETURN
        </Link>
      </div>

      <div>
        <div className="label-tel">{editing ? "MUTATION / UPDATE" : "INTAKE / CREATE"}</div>
        <h1
          className="font-display font-extrabold tracking-tighter-display leading-none mt-1"
          style={{ fontSize: "clamp(2rem, 5vw, 3rem)" }}
        >
          {editing ? "EDIT WORK ITEM" : "NEW WORK ITEM"}
        </h1>
      </div>

      <ErrorBanner message={error} />

      {editing && !loaded ? (
        <Panel title="LOADING">
          <SkeletonBlock rows={5} />
        </Panel>
      ) : (
        <form onSubmit={submit} className="space-y-5">
          <Panel title={headerLabel} subtitle="// payload">
            <div className="space-y-5">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                <Field label="TYPE" required>
                  <div className="flex gap-1.5 flex-wrap">
                    {ALL_TYPES.map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => update("type", t)}
                        className={`transition-opacity ${
                          form.type === t ? "opacity-100" : "opacity-40 hover:opacity-70"
                        }`}
                      >
                        <TypeBadge type={t} />
                      </button>
                    ))}
                  </div>
                </Field>
                <Field label="STATUS" required>
                  <select
                    value={form.status}
                    onChange={(e) => update("status", e.target.value)}
                    className={inputBase}
                  >
                    {ALL_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                  <div className="mt-2">
                    <StatusBadge status={form.status} />
                  </div>
                </Field>
              </div>

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
                  placeholder="// short, declarative summary"
                  className={`${inputBase} ${titleError ? "border-alert" : ""}`}
                />
              </Field>

              <Field
                label="BODY"
                hint={`${form.body.length} CHARS // OPTIONAL`}
              >
                <textarea
                  value={form.body}
                  onChange={(e) => update("body", e.target.value)}
                  rows={8}
                  placeholder="// full description, acceptance criteria, refs"
                  className={inputBase}
                />
              </Field>

              {editing && (
                <Field
                  label="ATTACHMENTS"
                  hint={uploading ? "UPLOADING…" : `${attachments.length} FILE(S) // PNG, JPG, GIF, WEBP, PDF // MAX 10MB`}
                >
                  <div className="space-y-3">
                    <div className="flex items-center gap-3">
                      <label className="inline-flex items-center gap-2 px-3 py-2 text-xs font-mono uppercase tracking-telemetry bg-canvas border border-edge-strong cursor-pointer hover:bg-raised transition-colors">
                        <input
                          type="file"
                          accept="image/png,image/jpeg,image/gif,image/webp,application/pdf"
                          onChange={handleFileSelect}
                          disabled={uploading}
                          className="hidden"
                        />
                        {uploading ? "UPLOADING…" : "+ ATTACH FILE"}
                      </label>
                      {uploadError && (
                        <span className="text-xs text-alert font-mono">! {uploadError}</span>
                      )}
                    </div>
                    {attachments.length > 0 && (
                      <div className="space-y-1.5">
                        {attachments.map((att) => (
                          <div
                            key={att.id}
                            className="flex items-center justify-between px-3 py-2 bg-canvas border border-edge text-xs font-mono"
                          >
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="text-fg-secondary">📎</span>
                              <span className="truncate text-fg-primary">{att.original_filename}</span>
                              <span className="text-fg-muted">({att.content_type})</span>
                            </div>
                            <span className="text-fg-muted tabular-nums">
                              {att.file_size > 1024 * 1024
                                ? `${(att.file_size / (1024 * 1024)).toFixed(1)} MB`
                                : `${Math.round(att.file_size / 1024)} KB`}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </Field>
              )}
            </div>
          </Panel>

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border border-edge bg-surface px-4 py-3">
            <div className="label-tel">
              {editing
                ? "MUTATIONS WILL OVERWRITE EXISTING RECORD"
                : "NEW RECORD WILL BE ASSIGNED AN ID"}
            </div>
            <div className="flex gap-2">
              <Link to={editing ? `/work-items/${id}` : "/work-items"}>
                <Button variant="ghost" type="button">CANCEL</Button>
              </Link>
              <Button
                variant="primary"
                type="submit"
                disabled={submitting || !!titleError}
              >
                {submitting
                  ? "WRITING…"
                  : editing
                  ? "SAVE CHANGES"
                  : "+ CREATE WORK ITEM"}
              </Button>
            </div>
          </div>
        </form>
      )}
    </div>
  );
}
