import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getJson, postJson } from "../api/client.js";

export default function WorkItemDetail() {
  const { id } = useParams();
  const [item, setItem] = useState(null);
  const [followUps, setFollowUps] = useState([]);
  const [error, setError] = useState(null);
  const [reason, setReason] = useState("");

  function refresh() {
    getJson(`/work-items/${id}`)
      .then(setItem)
      .catch((err) => setError(err.message));
    getJson(`/work-items/${id}/follow-ups`)
      .then(setFollowUps)
      .catch(() => undefined);
  }

  useEffect(() => {
    refresh();
  }, [id]);

  function approve() {
    postJson(`/work-items/${id}/approve`).then(refresh);
  }

  function block() {
    postJson(`/work-items/${id}/block`, { override_reason: reason }).then(
      refresh
    );
  }

  if (error) {
    return (
      <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">{error}</p>
    );
  }
  if (!item) return <p>Loading…</p>;

  return (
    <section className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold">{item.title}</h2>
          <p className="text-sm text-slate-500">
            #{item.id} · {item.type} · {item.status}
          </p>
        </div>
        <div className="flex gap-2">
          <Link
            to={`/work-items/${item.id}/edit`}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-100"
          >
            Edit
          </Link>
          <button
            onClick={approve}
            disabled={item.approved_by_operator}
            className="rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-60"
          >
            {item.approved_by_operator ? "Approved" : "Approve"}
          </button>
        </div>
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-600">Body</h3>
        <p className="mt-2 whitespace-pre-wrap text-sm">{item.body || "—"}</p>
      </div>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Detail label="Builder profile" value={item.builder_profile} />
        <Detail label="Builder model" value={item.builder_model} />
        <Detail label="Reviewer profile" value={item.reviewer_profile} />
        <Detail label="Reviewer model" value={item.reviewer_model} />
        <Detail label="PR URL" value={item.pr_url} />
        <Detail label="Merge commit" value={item.merge_commit_sha} />
        <Detail label="Approval timestamp" value={item.approval_timestamp} />
        <Detail label="Override reason" value={item.override_reason} />
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-600">Block</h3>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
          placeholder="Override reason"
          className="mt-2 w-full rounded-md border border-slate-300 p-2 text-sm"
        />
        <button
          onClick={block}
          className="mt-2 rounded-md bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-500"
        >
          Block
        </button>
      </div>

      <div className="rounded-md border border-slate-200 bg-white p-4">
        <h3 className="text-sm font-semibold text-slate-600">Follow-ups</h3>
        {followUps.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No follow-ups.</p>
        ) : (
          <ul className="mt-2 divide-y divide-slate-100">
            {followUps.map((fu) => (
              <li key={fu.id} className="py-2 text-sm">
                <span className="font-medium">{fu.title || "(untitled)"}</span>{" "}
                <span className="text-slate-500">
                  · {fu.severity} · {fu.status}
                </span>
                {fu.body && (
                  <p className="mt-1 text-slate-600">{fu.body}</p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Detail({ label, value }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-3">
      <div className="text-xs uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className="mt-1 text-sm">{value || "—"}</div>
    </div>
  );
}
