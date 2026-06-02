const VARIANTS = {
  primary:
    "bg-fg-primary text-canvas hover:bg-white border border-fg-primary",
  ghost:
    "bg-transparent text-fg-primary hover:bg-raised border border-edge-strong",
  danger:
    "bg-alert/10 text-alert hover:bg-alert/20 border border-alert/60",
  success:
    "bg-st-completed/10 text-st-completed hover:bg-st-completed/20 border border-st-completed/60",
};

const SIZES = {
  sm: "px-2.5 py-1 text-[11px]",
  md: "px-3 py-1.5 text-xs",
  lg: "px-4 py-2 text-sm",
};

export function Button({
  variant = "primary",
  size = "md",
  type = "button",
  disabled = false,
  className = "",
  children,
  ...rest
}) {
  return (
    <button
      type={type}
      disabled={disabled}
      className={
        `inline-flex items-center gap-1.5 font-mono uppercase tracking-telemetry font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${VARIANTS[variant]} ${SIZES[size]} ${className}`
      }
      {...rest}
    >
      {children}
    </button>
  );
}

export function OperatorButton({
  size = "sm",
  type = "button",
  disabled = false,
  className = "",
  children,
  ...rest
}) {
  // Operator action: dark canvas bg with phosphor green accent - for START BUILD and other primary operator actions
  // Default size 'sm' matches badge/button scale in header action area
  return (
    <button
      type={type}
      disabled={disabled}
      className={
        `inline-flex items-center gap-1.5 font-mono uppercase tracking-telemetry font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40 bg-canvas text-phosphor hover:bg-phosphor/10 border border-phosphor/60 ${SIZES[size]} ${className}`
      }
      {...rest}
    >
      {children}
    </button>
  );
}

export function LinkButton({ children, className = "", ...rest }) {
  return (
    <a
      className={
        `inline-flex items-center gap-1.5 font-mono uppercase tracking-telemetry font-semibold text-xs px-3 py-1.5 border border-edge-strong bg-transparent text-fg-primary hover:bg-raised transition-colors ${className}`
      }
      {...rest}
    >
      {children}
    </a>
  );
}
