import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getJson, postJson, putJson } from "../api/client.js";

const TYPE_OPTIONS = ["idea", "bug", "note", "task", "slice"];
const STATUS_OPTIONS = [
  "draft",
  "debated",
  "approved",
  "active",
  "review_needed",
  "certified",
  "pr_open",
  "ready_for_merge",
  "merged",
  "blocked",
  "completed",
];

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
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!editing) return;
    getJson(`/work-items/${id}`)
      .then((data) =>
        setForm({
          type: data.type,
          title: data.title,
          body: data.body || "",
          status: data.status,
        })
      )
      .catch((err) => setError(err.message));
  }, [id, editing]);

  function update(field, value) {
    setForm((prev) => ({ ...prev, [field]: value }));
  }

  function submit(event) {
    event.preventDefault();
    const promise = editing
      ? putJson(`/work-items/${id}`, form)
      : postJson("/work-items", form);
    promise
      .then((data) => navigate(`/work-items/${data.id}`))
      .catch((err) => setError(err.message));
  }

  return (
    <section className="max-w-xl">
      <h2 className="text-lg font-semibold">
        {editing ? "Edit Work Item" : "New Work Item"}
      </h2>
      {error && (
        <p className="mt-3 rounded-md bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}
      <form onSubmit={submit} className="mt-4 space-y-4">
        <Field label="Type">
          <select
            value={form.type}
            onChange={(e) => update("type", e.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1"
          >
            {TYPE_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Title">
          <input
            value={form.title}
            onChange={(e) => update("title", e.target.value)}
            required
            className="w-full rounded-md border border-slate-300 px-2 py-1"
          />
        </Field>
        <Field label="Body">
          <textarea
            value={form.body}
            onChange={(e) => update("body", e.target.value)}
            rows={6}
            className="w-full rounded-md border border-slate-300 p-2"
          />
        </Field>
        <Field label="Status">
          <select
            value={form.status}
            onChange={(e) => update("status", e.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
        </Field>
        <button
          type="submit"
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          {editing ? "Save" : "Create"}
        </button>
      </form>
    </section>
  );
}

function Field({ label, children }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-slate-600">{label}</span>
      {children}
    </label>
  );
}
