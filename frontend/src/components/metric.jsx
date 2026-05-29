export function MetricCard({ label, value, accent = "#EAEAEA", hint }) {
  return (
    <div className="border border-edge bg-surface px-4 py-3">
      <div className="label-tel">{label}</div>
      <div
        className="mt-1 font-mono text-3xl font-bold leading-none tracking-tighter-display"
        style={{ color: accent }}
      >
        {value}
      </div>
      {hint && (
        <div className="mt-2 label-tel" style={{ color: "#8A8A8A" }}>
          {hint}
        </div>
      )}
    </div>
  );
}

export function StatBar({ label, value, total, color }) {
  const pct = total > 0 ? Math.round((value / total) * 100) : 0;
  return (
    <div className="grid grid-cols-[120px_1fr_44px_44px] items-center gap-3">
      <div className="font-mono text-[11px] uppercase tracking-telemetry text-fg-secondary truncate">
        {label}
      </div>
      <div className="h-1.5 bg-canvas border border-edge">
        <div
          className="h-full"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <div className="font-mono text-xs text-fg-primary text-right tabular-nums">
        {value}
      </div>
      <div className="font-mono text-[10px] text-fg-muted text-right tabular-nums">
        {pct}%
      </div>
    </div>
  );
}
