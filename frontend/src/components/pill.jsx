import { statusMeta } from "./badges.jsx";

export function FilterPill({ active, onClick, color, label, count }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="group inline-flex items-center gap-2 border px-2.5 py-1 transition-colors"
      style={{
        borderColor: active ? color : "#262626",
        backgroundColor: active ? `${color}1F` : "transparent",
      }}
    >
      <span
        className="inline-block h-1.5 w-1.5"
        style={{ backgroundColor: color }}
      />
      <span
        className="font-mono uppercase tracking-telemetry text-[11px] font-semibold"
        style={{ color: active ? color : "#8A8A8A" }}
      >
        {label}
      </span>
      {typeof count === "number" && (
        <span
          className="font-mono text-[10px] tabular-nums"
          style={{ color: active ? color : "#5A5A5A" }}
        >
          {count}
        </span>
      )}
    </button>
  );
}

export function StatusPill({ status, active, onClick, count }) {
  const meta = statusMeta(status);
  return (
    <FilterPill
      active={active}
      onClick={onClick}
      color={meta.color}
      label={meta.label}
      count={count}
    />
  );
}
