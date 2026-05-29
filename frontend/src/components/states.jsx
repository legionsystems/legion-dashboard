export function SkeletonRow({ cols = 4 }) {
  return (
    <tr className="border-b border-edge">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-3 py-3">
          <div className="skeleton h-3 w-full" />
        </td>
      ))}
    </tr>
  );
}

export function SkeletonBlock({ rows = 4 }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="skeleton h-3 w-full" />
      ))}
    </div>
  );
}

export function EmptyState({
  title = "NO RECORDS",
  hint,
  action,
  glyph = "[ ∅ ]",
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 border border-dashed border-edge bg-canvas px-6 py-14 text-center">
      <div className="font-mono text-3xl text-fg-muted tracking-telemetry">
        {glyph}
      </div>
      <div className="label-tel-strong">{title}</div>
      {hint && (
        <p className="max-w-md text-sm text-fg-secondary">{hint}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ message }) {
  if (!message) return null;
  return (
    <div className="flex items-start gap-3 border border-alert/60 bg-alert/10 px-3 py-2 text-sm">
      <span className="font-mono text-alert">!!</span>
      <div className="flex-1">
        <div className="label-tel-strong text-alert">ERROR</div>
        <div className="mt-0.5 text-fg-primary break-words">{message}</div>
      </div>
    </div>
  );
}
