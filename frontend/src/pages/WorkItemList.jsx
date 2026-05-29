import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { getJson } from "../api/client.js";
import {
  StatusBadge,
  TypeBadge,
  ALL_STATUSES,
  ALL_TYPES,
} from "../components/badges.jsx";
import { Panel } from "../components/panel.jsx";
import { StatusPill, FilterPill } from "../components/pill.jsx";
import {
  EmptyState,
  ErrorBanner,
  SkeletonRow,
} from "../components/states.jsx";
import { Button } from "../components/buttons.jsx";

export default function WorkItemList() {
  const [items, setItems] = useState([]);
  const [type, setType] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams();
    if (type) params.set("type", type);
    if (status) params.set("status", status);
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
  }, [type, status]);

  const statusCounts = useMemo(() => {
    // counts reflect currently-loaded set (server already filtered by status if applied)
    const c = {};
    for (const it of items) c[it.status] = (c[it.status] || 0) + 1;
    return c;
  }, [items]);

  const filtered = useMemo(() => {
    if (!query.trim()) return items;
    const q = query.toLowerCase();
    return items.filter(
      (it) =>
        it.title.toLowerCase().includes(q) ||
        String(it.id).includes(q) ||
        (it.body || "").toLowerCase().includes(q),
    );
  }, [items, query]);

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
        title="FILTER / STATUS"
        subtitle={status ? `// ${status}` : "// all"}
        right={
          status && (
            <button
              onClick={() => setStatus("")}
              className="font-mono uppercase tracking-telemetry text-[11px] font-semibold text-fg-secondary hover:text-fg-primary"
            >
              CLEAR ×
            </button>
          )
        }
      >
        <div className="flex flex-wrap gap-1.5">
          <FilterPill
            active={status === ""}
            onClick={() => setStatus("")}
            color="#EAEAEA"
            label="ALL"
          />
          {ALL_STATUSES.map((s) => (
            <StatusPill
              key={s}
              status={s}
              active={status === s}
              onClick={() => setStatus(status === s ? "" : s)}
              count={statusCounts[s]}
            />
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
                  <th className="label-tel px-3 py-2 w-[80px]">ID</th>
                  <th className="label-tel px-3 py-2 w-[110px]">TYPE</th>
                  <th className="label-tel px-3 py-2">TITLE</th>
                  <th className="label-tel px-3 py-2 w-[140px]">STATUS</th>
                  <th className="label-tel px-3 py-2 w-[90px] text-right">APPRV</th>
                </tr>
              </thead>
              <tbody>
                {loading &&
                  Array.from({ length: 5 }).map((_, i) => (
                    <SkeletonRow key={i} cols={5} />
                  ))}
                {!loading &&
                  filtered.map((item) => (
                    <tr
                      key={item.id}
                      className="border-b border-edge/60 hover:bg-raised transition-colors group"
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
                      </td>
                      <td className="px-3 py-2.5">
                        <StatusBadge status={item.status} />
                      </td>
                      <td className="px-3 py-2.5 text-right">
                        {item.approved_by_operator ? (
                          <span className="font-mono text-[10px] tracking-telemetry text-st-completed">
                            ✓ OK
                          </span>
                        ) : (
                          <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
                            —
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
