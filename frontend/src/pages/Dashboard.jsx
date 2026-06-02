import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getJson } from "../api/client.js";
import { Panel } from "../components/panel.jsx";
import { StatusBadge, TypeBadge, statusMeta } from "../components/badges.jsx";
import { MetricCard, StatBar } from "../components/metric.jsx";
import {
  EmptyState,
  ErrorBanner,
  SkeletonBlock,
} from "../components/states.jsx";
import { LinkButton, Button } from "../components/buttons.jsx";

const STATUS_GROUPS = [
  ["draft", "debated"],
  ["approved", "active"],
  ["review_needed", "certified"],
  ["pr_open", "ready_for_merge"],
  ["merged", "completed"],
  ["blocked"],
];

function formatTime(iso) {
  if (!iso) return "—";
  // Display in user's local timezone (Sydney: Australia/Sydney)
  const d = new Date(iso);
  return d.toLocaleString('en-AU', {
    timeZone: 'Australia/Sydney',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).replace(',', '');
}

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getJson("/stats")
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const total = stats?.total ?? 0;
  const awaiting = stats?.awaiting_approval ?? 0;
  const blocked = stats?.by_status?.blocked ?? 0;
  const inFlight =
    (stats?.by_status?.active ?? 0) +
    (stats?.by_status?.pr_open ?? 0) +
    (stats?.by_status?.review_needed ?? 0);

  return (
    <div className="space-y-6">
      <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-3">
        <div>
          <div className="label-tel">SECTOR / OVERVIEW</div>
          <h1
            className="font-display font-extrabold tracking-tighter-display leading-none mt-1"
            style={{ fontSize: "clamp(2rem, 5vw, 3rem)" }}
          >
            DASHBOARD
          </h1>
          <p className="mt-2 text-sm text-fg-secondary max-w-xl">
            Operator console for the LEGION work-item pipeline. Live counts,
            queues, and recent transitions.
          </p>
        </div>
        <div className="flex gap-2 shrink-0">
          <Link to="/work-items">
            <Button variant="ghost" size="md">VIEW QUEUE &gt;</Button>
          </Link>
          <Link to="/work-items/new">
            <Button variant="primary" size="md">+ NEW WORK ITEM</Button>
          </Link>
        </div>
      </div>

      <ErrorBanner message={error} />

      <div className="grid grid-cols-2 md:grid-cols-4 gap-px bg-edge border border-edge">
        <MetricCard
          label="TOTAL WORK ITEMS"
          value={stats ? total : "—"}
          accent="#EAEAEA"
        />
        <MetricCard
          label="AWAITING APPROVAL"
          value={stats ? awaiting : "—"}
          accent={awaiting > 0 ? "#F97316" : "#5A5A5A"}
          hint={awaiting > 0 ? "OPERATOR ACTION REQUIRED" : "QUEUE CLEAR"}
        />
        <MetricCard
          label="IN FLIGHT"
          value={stats ? inFlight : "—"}
          accent={inFlight > 0 ? "#F59E0B" : "#5A5A5A"}
          hint="ACTIVE + REVIEW + PR_OPEN"
        />
        <MetricCard
          label="BLOCKED"
          value={stats ? blocked : "—"}
          accent={blocked > 0 ? "#EF4444" : "#5A5A5A"}
          hint={blocked > 0 ? "INTERVENTION NEEDED" : "NONE"}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Panel
          title="STATUS DISTRIBUTION"
          subtitle="// by lifecycle stage"
          className="lg:col-span-3"
        >
          {!stats && !error && <SkeletonBlock rows={6} />}
          {stats && (
            <div className="space-y-2">
              {STATUS_GROUPS.flat().map((s) => {
                const meta = statusMeta(s);
                const v = stats.by_status?.[s] ?? 0;
                return (
                  <StatBar
                    key={s}
                    label={meta.label}
                    value={v}
                    total={Math.max(total, 1)}
                    color={meta.color}
                  />
                );
              })}
            </div>
          )}
        </Panel>

        <Panel
          title="TYPE BREAKDOWN"
          subtitle="// by work kind"
          className="lg:col-span-2"
        >
          {!stats && !error && <SkeletonBlock rows={5} />}
          {stats && (
            <div className="space-y-3">
              {Object.entries(stats.by_type || {}).map(([type, count]) => (
                <div
                  key={type}
                  className="flex items-center justify-between border-b border-edge/60 pb-2 last:border-b-0 last:pb-0"
                >
                  <TypeBadge type={type} />
                  <span className="font-mono text-lg font-bold tabular-nums">
                    {count}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title="RECENT ACTIVITY"
        subtitle="// last 8 updates"
        right={
          <Link
            to="/work-items"
            className="font-mono uppercase tracking-telemetry text-[11px] font-semibold text-fg-secondary hover:text-fg-primary"
          >
            ALL &gt;
          </Link>
        }
      >
        {!stats && !error && <SkeletonBlock rows={5} />}
        {stats && stats.recent.length === 0 && (
          <EmptyState
            title="NO ACTIVITY YET"
            hint="Create a work item to populate the pipeline."
            action={
              <Link to="/work-items/new">
                <Button variant="primary" size="md">+ NEW WORK ITEM</Button>
              </Link>
            }
          />
        )}
        {stats && stats.recent.length > 0 && (
          <ul className="divide-y divide-edge/60">
            {stats.recent.map((r) => (
              <li key={r.id}>
                <Link
                  to={`/work-items/${r.id}`}
                  className="grid grid-cols-[60px_110px_1fr_auto_auto] items-center gap-3 py-2.5 hover:bg-raised -mx-4 px-4 transition-colors"
                >
                  <span className="font-mono text-xs text-fg-muted tabular-nums">
                    #{String(r.id).padStart(4, "0")}
                  </span>
                  <TypeBadge type={r.type} />
                  <span className="text-sm text-fg-primary truncate">
                    {r.title}
                  </span>
                  <StatusBadge status={r.status} />
                  <span className="font-mono text-[10px] text-fg-muted tabular-nums hidden md:inline">
                    {formatTime(r.updated_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
