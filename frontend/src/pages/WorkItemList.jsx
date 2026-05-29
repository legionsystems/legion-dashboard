import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getJson } from "../api/client.js";

const TYPE_OPTIONS = ["", "idea", "bug", "note", "task", "slice"];
const STATUS_OPTIONS = [
  "",
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

export default function WorkItemList() {
  const [items, setItems] = useState([]);
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams();
    if (type) params.set("type", type);
    if (status) params.set("status", status);
    const query = params.toString();
    setLoading(true);
    getJson(`/work-items${query ? `?${query}` : ""}`)
      .then((data) => {
        setItems(data);
        setError(null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [type, status]);

  return (
    <section>
      <div className="flex items-end justify-between gap-4">
        <h2 className="text-lg font-semibold">Work Items</h2>
        <Link
          to="/work-items/new"
          className="rounded-md bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700"
        >
          New Work Item
        </Link>
      </div>

      <div className="mt-4 flex flex-wrap gap-3">
        <label className="text-sm">
          <span className="mr-2 text-slate-600">Type</span>
          <select
            value={type}
            onChange={(e) => setType(e.target.value)}
            className="rounded-md border border-slate-300 bg-white px-2 py-1"
          >
            {TYPE_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt || "all"}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          <span className="mr-2 text-slate-600">Status</span>
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-slate-300 bg-white px-2 py-1"
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt || "all"}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 p-3 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="mt-4 overflow-hidden rounded-md border border-slate-200 bg-white">
        <table className="min-w-full divide-y divide-slate-200 text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-4 py-2">ID</th>
              <th className="px-4 py-2">Type</th>
              <th className="px-4 py-2">Title</th>
              <th className="px-4 py-2">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {loading && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-slate-500">
                  Loading…
                </td>
              </tr>
            )}
            {!loading && items.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-slate-500">
                  No work items.
                </td>
              </tr>
            )}
            {items.map((item) => (
              <tr key={item.id} className="hover:bg-slate-50">
                <td className="px-4 py-2 text-slate-500">{item.id}</td>
                <td className="px-4 py-2">{item.type}</td>
                <td className="px-4 py-2">
                  <Link
                    to={`/work-items/${item.id}`}
                    className="text-slate-900 hover:underline"
                  >
                    {item.title}
                  </Link>
                </td>
                <td className="px-4 py-2">{item.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
