import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getJson } from "../api/client.js";
import {
  StatusBadge,
  TypeBadge,
  ALL_TYPES,
  LIFECYCLE_FILTERS,
} from "../components/badges.jsx";
import { Panel } from "../components/panel.jsx";
import { FilterPill } from "../components/pill.jsx";
import {
  EmptyState,
  ErrorBanner,
  SkeletonRow,
} from "../components/states.jsx";
import { Button } from "../components/buttons.jsx";

// Mirrors backend models.DEBATE_ELIGIBLE_STATUSES.
const DEBATE_ELIGIBLE_STATUSES = new Set([
  "draft",
  "awaiting_approval",
  "pending_approval",
  "review",
  "review_needed",
  "ready_for_approval",
]);

const RUN_STATUS_COLOR = {
  queued: "#F59E0B",
  running: "#3B82F6",
  completed: "#10B981",
  failed: "#EF4444",
};

// Debate recommendations. APPROVE_WITH_MANDATORY_EDITS / APPROVE_WITH_EDITS
// are given a distinct amber/orange palette so a glance at the row makes it
// obvious that required work remains, rather than reading like a clean
// approval. Backend canonical values live in
// ``backend/app/debate_executor.py`` (APPROVE_AS_IS, APPROVE_WITH_MANDATORY_EDITS,
// NEEDS_REWORK, SPLIT_SCOPE, DEFER, REJECT, LOW_SIGNAL); the legacy
// SPLIT_FIRST/NEEDS_MORE_DETAIL/DO_NOT_BUILD_NOW values are kept for old runs.
const RECOMMENDATION_META = {
  APPROVE_AS_IS: { label: "APPROVE", color: "#10B981" },
  APPROVE_WITH_EDITS: { label: "APPROVE+EDITS REQUIRED", color: "#F97316" },
  APPROVE_WITH_MANDATORY_EDITS: {
    label: "APPROVE+EDITS REQUIRED",
    color: "#F97316",
  },
  NEEDS_REWORK: { label: "NEEDS REWORK", color: "#F97316" },
  SPLIT_SCOPE: { label: "SPLIT SCOPE", color: "#A855F7" },
  SPLIT_FIRST: { label: "SPLIT FIRST", color: "#A855F7" },
  NEEDS_MORE_DETAIL: { label: "MORE DETAIL", color: "#F59E0B" },
  DEFER: { label: "DEFER", color: "#8A8A8A" },
  REJECT: { label: "REJECT", color: "#EF4444" },
  DO_NOT_BUILD_NOW: { label: "DO NOT BUILD", color: "#EF4444" },
  LOW_SIGNAL: { label: "LOW SIGNAL", color: "#8A8A8A" },
};

// Fallback for any debate recommendation not in the table above — surfaces
// the raw value rather than hiding it.
function recommendationMetaFor(rec) {
  if (!rec) return null;
  if (RECOMMENDATION_META[rec]) return RECOMMENDATION_META[rec];
  return {
    label: String(rec).replace(/_/g, " ").toUpperCase(),
    color: "#8A8A8A",
  };
}

// Lifecycle bucket → set of effective_state values it explicitly contains.
// BLOCKED / REVIEW_REQUIRED / PARKED / COMPLETED are explicit sets; ACTIVE is
// the residual bucket — any state not matched by the explicit sets falls
// here. This guarantees every backend state (including raw Kanban fallbacks
// like ``pr_open`` or ``review_needed`` and any future ``compute_effective_state``
// values) is reachable from at least one filter, so a row never disappears
// from every pill except ``ALL``.
const EXPLICIT_LIFECYCLE_BUCKETS = {
  blocked: new Set([
    "blocked",
    "blocked_merge",
    "merged_deployment_failed",
  ]),
  review_required: new Set([
    "in_review",
    "review_needed",
    "code_reviewed",
    "changes_requested",
    "review_failed",
    "needs_rework",
    "preview_pending",
    "preview_ready",
    "certified",
    "ready_to_merge",
    "pr_open",
    "ready_for_merge",
  ]),
  parked: new Set(["archived", "rejected"]),
  completed: new Set(["complete", "merged"]),
};

function bucketFor(state) {
  for (const [key, set] of Object.entries(EXPLICIT_LIFECYCLE_BUCKETS)) {
    if (set.has(state)) return key;
  }
  return "active";
}

// Map effective_state → one-word/short next action the operator should take.
// The Next Action column reads from this so an operator can scan the list
// without opening each item (WI-32 acceptance criterion).
const NEXT_ACTION = {
  drafting: "DEBATE",
  debating: "WAIT DEBATE",
  // debated falls back to debate recommendation (see nextActionFor)
  debated: "REVIEW DEBATE",
  approved: "SEND TO BUILDER",
  building: "WAIT BUILD",
  implemented: "REVIEW CONTRACT",
  in_review: "REVIEW PR",
  review_needed: "REVIEW",
  pr_open: "REVIEW PR",
  ready_for_merge: "MERGE",
  code_reviewed: "CERTIFY",
  changes_requested: "REWORK",
  review_failed: "REWORK",
  needs_rework: "REWORK",
  preview_pending: "DEPLOY PREVIEW",
  preview_ready: "CERTIFY",
  certified: "MERGE",
  ready_to_merge: "MERGE",
  blocked: "UNBLOCK",
  blocked_merge: "RETRY MERGE",
  merged: "COMPLETE",
  merged_deployment_failed: "FIX DEPLOY",
  complete: "—",
  rejected: "PARK / CLEANUP",
  archived: "—",
};

function nextActionFor(item) {
  const state = item.effective_state || item.status || "";
  // When the debate just finished and no later signal has arrived, the next
  // action depends on the debate's final recommendation rather than the
  // generic "REVIEW DEBATE" fallback.
  if (state === "debated" && item.latest_debate) {
    const rec = item.latest_debate.final_recommendation;
    if (rec === "APPROVE_AS_IS") return "APPROVE";
    if (rec === "APPROVE_WITH_EDITS" || rec === "APPROVE_WITH_MANDATORY_EDITS")
      return "APPROVE + APPLY EDITS";
    if (rec === "SPLIT_FIRST" || rec === "SPLIT_SCOPE") return "SPLIT FIRST";
    if (rec === "NEEDS_MORE_DETAIL") return "ADD DETAIL";
    if (rec === "NEEDS_REWORK") return "REWORK";
    if (rec === "DO_NOT_BUILD_NOW" || rec === "REJECT") return "PARK";
    if (rec === "DEFER") return "DEFER";
    if (rec === "LOW_SIGNAL") return "RE-DEBATE";
  }
  return NEXT_ACTION[state] || "—";
}

// Render the debate cell — status of the latest run + final recommendation.
function DebateCell({ item }) {
  const latest = item.latest_debate;
  if (!latest) {
    const needs = DEBATE_ELIGIBLE_STATUSES.has(item.status);
    return needs ? (
      <span
        className="font-mono text-[10px] tracking-telemetry border px-1.5 py-0.5"
        style={{
          color: "#F59E0B",
          borderColor: "#F59E0B55",
          backgroundColor: "#F59E0B14",
        }}
      >
        NEEDS DEBATE
      </span>
    ) : (
      <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
        —
      </span>
    );
  }
  const color = RUN_STATUS_COLOR[latest.status] || "#5A5A5A";
  const rec = recommendationMetaFor(latest.final_recommendation);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span
        className="inline-flex items-center gap-1 font-mono uppercase tracking-telemetry text-[10px] font-semibold border px-1.5 py-0.5"
        style={{
          color,
          borderColor: `${color}55`,
          backgroundColor: `${color}14`,
        }}
      >
        <span
          className="inline-block h-1.5 w-1.5"
          style={{ backgroundColor: color }}
        />
        DEBATE: {latest.status.toUpperCase()}
      </span>
      {rec && (
        <span
          className="font-mono uppercase tracking-telemetry text-[10px] font-semibold border px-1.5 py-0.5"
          style={{
            color: rec.color,
            borderColor: `${rec.color}55`,
            backgroundColor: `${rec.color}14`,
          }}
        >
          {rec.label}
        </span>
      )}
    </div>
  );
}

// Implementation column — what does the builder say about this work item?
// Reads the raw Kanban status (which the builder updates as it moves through
// active → completed) plus operator approval. Deliberately distinct from the
// Work Status column so an item with completed debate but no build does not
// look done.
function ImplementationCell({ item }) {
  const state = item.effective_state || "";
  const status = (item.status || "").toLowerCase();
  if (item.merge_commit_sha) {
    return (
      <span className="font-mono text-[10px] tracking-telemetry text-fg-secondary">
        MERGED · {String(item.merge_commit_sha).slice(0, 7)}
      </span>
    );
  }
  if (status === "active" || status === "building" || status === "in_progress") {
    return (
      <span
        className="font-mono uppercase tracking-telemetry text-[10px] font-semibold border px-1.5 py-0.5"
        style={{
          color: "#F59E0B",
          borderColor: "#F59E0B55",
          backgroundColor: "#F59E0B14",
        }}
      >
        BUILDING
      </span>
    );
  }
  if (status === "completed" || status === "implemented" || status === "done") {
    return (
      <span
        className="font-mono uppercase tracking-telemetry text-[10px] font-semibold border px-1.5 py-0.5"
        style={{
          color: "#FACC15",
          borderColor: "#FACC1555",
          backgroundColor: "#FACC1514",
        }}
      >
        IMPLEMENTED
      </span>
    );
  }
  if (item.approved_by_operator && state !== "drafting") {
    return (
      <span
        className="font-mono uppercase tracking-telemetry text-[10px] font-semibold border px-1.5 py-0.5"
        style={{
          color: "#3B82F6",
          borderColor: "#3B82F655",
          backgroundColor: "#3B82F614",
        }}
      >
        READY TO BUILD
      </span>
    );
  }
  return (
    <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
      —
    </span>
  );
}

// Review / PR column — surfaces PR, code review verdict, and preview state.
function ReviewCell({ item }) {
  const parts = [];
  const cr = (item.code_review_status || "").toLowerCase();
  if (cr === "approved") {
    parts.push({ color: "#10B981", label: "REVIEW: APPROVED" });
  } else if (cr === "changes_requested" || cr === "changes-requested") {
    parts.push({ color: "#F97316", label: "REVIEW: CHANGES" });
  } else if (cr === "failed") {
    parts.push({ color: "#EF4444", label: "REVIEW: FAILED" });
  }
  if (item.preview_deployed) {
    parts.push({ color: "#06B6D4", label: "PREVIEW: READY" });
  } else if (item.preview_required) {
    parts.push({ color: "#F59E0B", label: "PREVIEW: PENDING" });
  }
  if (item.pr_number || item.pr_url) {
    const label = item.pr_number ? `PR #${item.pr_number}` : "PR";
    parts.push({ color: "#6366F1", label });
  }
  if (item.operator_certified) {
    parts.push({ color: "#14B8A6", label: "CERTIFIED" });
  }
  if (item.ready_to_merge) {
    parts.push({ color: "#06B6D4", label: "READY MERGE" });
  }
  if (!parts.length) {
    return (
      <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
        —
      </span>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-1">
      {parts.map((p, i) => (
        <span
          key={i}
          className="inline-flex items-center gap-1 font-mono uppercase tracking-telemetry text-[10px] font-semibold border px-1.5 py-0.5"
          style={{
            color: p.color,
            borderColor: `${p.color}55`,
            backgroundColor: `${p.color}14`,
          }}
        >
          {p.label}
        </span>
      ))}
    </div>
  );
}

// Next Action column. Most actions are neutral grey; "—" (no action) is
// muted; rework/blocked actions get a warning tint so they stand out.
function NextActionCell({ item }) {
  const label = nextActionFor(item);
  const state = item.effective_state || "";
  let color = "#EAEAEA";
  if (label === "—") color = "#5A5A5A";
  else if (
    state === "blocked" ||
    state === "blocked_merge" ||
    state === "merged_deployment_failed" ||
    state === "changes_requested" ||
    state === "review_failed" ||
    state === "needs_rework"
  ) {
    color = "#F97316";
  } else if (state === "rejected" || state === "archived") {
    color = "#8A8A8A";
  } else if (
    state === "approved" ||
    state === "ready_to_merge" ||
    state === "certified" ||
    state === "merged" ||
    state === "code_reviewed"
  ) {
    color = "#06B6D4";
  }
  return (
    <span
      className="inline-flex items-center font-mono uppercase tracking-telemetry text-[10px] font-semibold border px-1.5 py-0.5"
      style={{
        color,
        borderColor: `${color}55`,
        backgroundColor: `${color}14`,
      }}
    >
      {label}
    </span>
  );
}

export default function WorkItemList() {
  const [items, setItems] = useState([]);
  const [type, setType] = useState("");
  const [lifecycle, setLifecycle] = useState("active"); // bucket key
  const [generated, setGenerated] = useState("all"); // human | system | test | all
  const [app, setApp] = useState(""); // "" = All | app_id | __unassigned__
  const [query, setQuery] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [apps, setApps] = useState([]);

  useEffect(() => {
    getJson("/work-items/apps")
      .then((data) => setApps(data))
      .catch(() => {});
  }, []);

  // Always fetch the full set (including archived/parked rows) so the
  // lifecycle pill counts and the ``ALL`` filter are accurate regardless of
  // which bucket is currently selected. Client-side bucketing then narrows
  // the rendered list. Work item counts are small enough that this is
  // cheaper than juggling separate per-bucket count queries.
  useEffect(() => {
    const params = new URLSearchParams();
    if (type) params.set("type", type);
    params.set("view", "all");
    if (generated) params.set("generated", generated);
    if (app) params.set("app", app);
    const qs = params.toString();
    setLoading(true);
    let cancelled = false;
    getJson(`/work-items${qs ? `?${qs}` : ""}`)
      .then((data) => {
        if (!cancelled) {
          setItems(data);
          setError(null);
        }
      })
      .catch((err) => !cancelled && setError(err.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [type, generated, app]);

  // Client-side projection of the loaded items into lifecycle buckets. ACTIVE
  // is the residual — any state not in BLOCKED/REVIEW_REQUIRED/PARKED/COMPLETED
  // lands here, including raw Kanban fallbacks the lifecycle module may emit.
  const lifecycleCounts = useMemo(() => {
    const c = { active: 0, blocked: 0, review_required: 0, parked: 0, completed: 0, all: 0 };
    for (const it of items) {
      c.all += 1;
      const state = it.effective_state || it.status || "";
      c[bucketFor(state)] += 1;
    }
    return c;
  }, [items]);

  const bucketed = useMemo(() => {
    if (lifecycle === "all") return items;
    return items.filter(
      (it) => bucketFor(it.effective_state || it.status || "") === lifecycle,
    );
  }, [items, lifecycle]);

  const filtered = useMemo(() => {
    if (!query.trim()) return bucketed;
    const q = query.toLowerCase();
    return bucketed.filter(
      (it) =>
        it.title.toLowerCase().includes(q) ||
        String(it.id).includes(q) ||
        (it.body || "").toLowerCase().includes(q),
    );
  }, [bucketed, query]);

  return (
    <div className="space-y-5">
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3">
        <div>
          <div className="label-tel">REGISTRY / QUEUE</div>
          <h1
            className="font-display font-extrabold tracking-tighter-display leading-none mt-1"
            style={{ fontSize: "clamp(2rem, 5vw, 3rem)" }}
          >
            WORK ITEMS
          </h1>
        </div>
        <div className="flex gap-2 shrink-0">
          <Link to="/work-items/new">
            <Button variant="primary" size="md">+ NEW WORK ITEM</Button>
          </Link>
        </div>
      </div>

      <ErrorBanner message={error} />

      <Panel
        title="FILTER / LIFECYCLE"
        subtitle={`// ${lifecycle.replace("_", " ")}`}
      >
        <div className="flex flex-wrap gap-1.5">
          {LIFECYCLE_FILTERS.map((f) => (
            <FilterPill
              key={f.key}
              active={lifecycle === f.key}
              onClick={() => setLifecycle(f.key)}
              color={f.color}
              label={f.label}
              count={
                f.key === "all"
                  ? lifecycleCounts.all
                  : lifecycleCounts[f.key]
              }
            />
          ))}
        </div>
      </Panel>

      <Panel title="FILTER / ORIGIN" subtitle={`// ${generated}`}>
        <div className="flex items-center gap-1.5">
          <span className="label-tel">ORIGIN:</span>
          {["human", "system", "test", "all"].map((g) => (
            <button
              key={g}
              onClick={() => setGenerated(g)}
              className={`px-2 py-1 font-mono uppercase tracking-telemetry text-[10px] font-semibold border ${
                generated === g
                  ? "border-fg-primary text-fg-primary bg-raised"
                  : "border-edge text-fg-secondary hover:text-fg-primary"
              }`}
            >
              {g.toUpperCase()}
            </button>
          ))}
        </div>
      </Panel>

      <div className="flex flex-col md:flex-row gap-3">
        <div className="flex items-center gap-2 border border-edge bg-surface px-3 py-2 flex-1">
          <span className="font-mono text-fg-muted text-xs">&gt;</span>
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="SEARCH BY TITLE, ID, OR BODY"
            className="flex-1 bg-transparent border-none outline-none font-mono uppercase tracking-telemetry text-xs placeholder:text-fg-muted"
            style={{ boxShadow: "none" }}
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="font-mono text-fg-muted text-xs hover:text-fg-primary"
            >
              ×
            </button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className="label-tel">TYPE:</span>
          <div className="flex gap-1.5">
            <FilterPill
              active={type === ""}
              onClick={() => setType("")}
              color="#EAEAEA"
              label="ALL"
            />
            {ALL_TYPES.map((t) => (
              <FilterPill
                key={t}
                active={type === t}
                onClick={() => setType(type === t ? "" : t)}
                color="#EAEAEA"
                label={t.toUpperCase()}
              />
            ))}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="label-tel">APP:</span>
          <select
            value={app}
            onChange={(e) => setApp(e.target.value)}
            className="bg-surface border border-edge font-mono uppercase tracking-telemetry text-[11px] text-fg-secondary px-2 py-1 outline-none"
          >
            <option value="">ALL</option>
            <option value="__unassigned__">NO APP</option>
            {apps.map((a) => (
              <option key={a} value={a}>{a.toUpperCase()}</option>
            ))}
          </select>
        </div>
      </div>

      <section className="border border-edge bg-surface">
        <header className="flex items-center justify-between border-b border-edge px-4 py-2">
          <h3 className="label-tel-strong">[ ITEMS ]</h3>
          <span className="label-tel tabular-nums">
            {loading ? "LOADING…" : `${filtered.length} / ${items.length}`}
          </span>
        </header>

        {!loading && filtered.length === 0 && (
          <div className="p-4">
            <EmptyState
              title={
                items.length === 0
                  ? "REGISTRY EMPTY"
                  : "NO MATCHES FOR FILTER"
              }
              hint={
                items.length === 0
                  ? "No work items in the system yet. Create one to begin."
                  : "Adjust filters or clear the search to widen results."
              }
              action={
                items.length === 0 ? (
                  <Link to="/work-items/new">
                    <Button variant="primary" size="md">+ NEW WORK ITEM</Button>
                  </Link>
                ) : null
              }
            />
          </div>
        )}

        {(loading || filtered.length > 0) && (
          <div className="overflow-x-auto">
            <table className="min-w-full border-collapse">
              <thead>
                <tr className="border-b border-edge text-left">
                  <th className="label-tel px-3 py-2 w-[70px]">ID</th>
                  <th className="label-tel px-3 py-2 w-[100px]">TYPE</th>
                  <th className="label-tel px-3 py-2">TITLE</th>
                  <th className="label-tel px-3 py-2 w-[150px]">WORK STATUS</th>
                  <th className="label-tel px-3 py-2 w-[220px]">DEBATE</th>
                  <th className="label-tel px-3 py-2 w-[140px]">IMPLEMENTATION</th>
                  <th className="label-tel px-3 py-2 w-[210px]">REVIEW / PR</th>
                  <th className="label-tel px-3 py-2 w-[160px]">NEXT ACTION</th>
                </tr>
              </thead>
              <tbody>
                {loading &&
                  Array.from({ length: 5 }).map((_, i) => (
                    <SkeletonRow key={i} cols={8} />
                  ))}
                {!loading &&
                  filtered.map((item) => {
                    const state = item.effective_state || item.status || "";
                    const isParked =
                      state === "archived" || state === "rejected";
                    return (
                      <tr
                        key={item.id}
                        className={`border-b border-edge/60 hover:bg-raised transition-colors group ${
                          isParked ? "opacity-60" : ""
                        }`}
                      >
                        <td className="px-3 py-2.5 font-mono text-xs text-fg-muted tabular-nums">
                          #{String(item.id).padStart(4, "0")}
                        </td>
                        <td className="px-3 py-2.5">
                          <TypeBadge type={item.type} />
                        </td>
                        <td className="px-3 py-2.5">
                          <Link
                            to={`/work-items/${item.id}`}
                            className="text-fg-primary group-hover:underline decoration-fg-primary/40 underline-offset-4"
                          >
                            {item.title}
                          </Link>
                          {item.body && (
                            <p className="text-xs text-fg-muted mt-0.5 line-clamp-1">
                              {item.body}
                            </p>
                          )}
                          <div className="flex gap-1 mt-1 flex-wrap">
                            {item.archived && (
                              <span
                                className="inline-flex items-center gap-1 border px-1 py-0.5 text-[9px] font-mono uppercase tracking-telemetry font-semibold"
                                style={{
                                  color: "#8A8A8A",
                                  borderColor: "#8A8A8A55",
                                  backgroundColor: "#8A8A8A14",
                                }}
                              >
                                ARCHIVED
                              </span>
                            )}
                            {item.is_system_generated && (
                              <span
                                className="inline-flex items-center gap-1 border px-1 py-0.5 text-[9px] font-mono uppercase tracking-telemetry font-semibold"
                                style={{
                                  color: "#A855F7",
                                  borderColor: "#A855F755",
                                  backgroundColor: "#A855F714",
                                }}
                              >
                                SYSTEM
                              </span>
                            )}
                            {item.is_test_item && (
                              <span
                                className="inline-flex items-center gap-1 border px-1 py-0.5 text-[9px] font-mono uppercase tracking-telemetry font-semibold"
                                style={{
                                  color: "#F59E0B",
                                  borderColor: "#F59E0B55",
                                  backgroundColor: "#F59E0B14",
                                }}
                              >
                                TEST
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-3 py-2.5">
                          <StatusBadge status={state} />
                        </td>
                        <td className="px-3 py-2.5">
                          <DebateCell item={item} />
                        </td>
                        <td className="px-3 py-2.5">
                          <ImplementationCell item={item} />
                        </td>
                        <td className="px-3 py-2.5">
                          <ReviewCell item={item} />
                        </td>
                        <td className="px-3 py-2.5">
                          <NextActionCell item={item} />
                        </td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
