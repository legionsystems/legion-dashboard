export function Panel({ title, subtitle, right, children, className = "" }) {
  return (
    <section
      className={`border border-edge bg-surface ${className}`}
    >
      {(title || right) && (
        <header className="flex items-center justify-between gap-3 border-b border-edge px-4 py-2.5">
          <div className="flex items-baseline gap-3 min-w-0">
            {title && (
              <h3 className="label-tel-strong truncate">[ {title} ]</h3>
            )}
            {subtitle && (
              <span className="label-tel truncate">{subtitle}</span>
            )}
          </div>
          {right && <div className="flex items-center gap-2 shrink-0">{right}</div>}
        </header>
      )}
      <div className="px-4 py-3">{children}</div>
    </section>
  );
}

export function FieldRow({ label, value, mono = false }) {
  return (
    <div className="grid grid-cols-[140px_1fr] gap-3 border-b border-edge/60 py-2 last:border-b-0">
      <div className="label-tel pt-0.5">{label}</div>
      <div
        className={
          (mono ? "font-mono " : "") +
          (value ? "text-fg-primary" : "text-fg-muted") +
          " text-sm break-words"
        }
      >
        {value || "—"}
      </div>
    </div>
  );
}
