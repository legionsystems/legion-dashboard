import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { getJson } from "../api/client.js";

function nowStamp() {
  const d = new Date();
  return d.toISOString().split(".")[0].replace("T", " ") + "Z";
}

function Brand() {
  return (
    <div className="border-b border-edge px-4 py-4">
      <div className="flex items-baseline gap-2">
        <span
          className="font-display font-extrabold tracking-tighter-display text-fg-primary leading-none"
          style={{ fontSize: "1.5rem" }}
        >
          LEGION
        </span>
        <span className="label-tel">®</span>
      </div>
      <div className="mt-1 label-tel">CONTROL PLANE / v0.1</div>
    </div>
  );
}

function NavItem({ to, end = false, label, glyph }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        `group flex items-center gap-3 border-l-2 px-4 py-2.5 transition-colors ${
          isActive
            ? "border-fg-primary bg-raised text-fg-primary"
            : "border-transparent text-fg-secondary hover:bg-surface hover:text-fg-primary"
        }`
      }
    >
      <span className="font-mono text-xs w-4 text-center opacity-70 group-hover:opacity-100">
        {glyph}
      </span>
      <span className="font-mono uppercase tracking-telemetry text-xs font-semibold">
        {label}
      </span>
    </NavLink>
  );
}

function HealthIndicator() {
  const [health, setHealth] = useState({ ok: null });

  useEffect(() => {
    let cancelled = false;
    function ping() {
      getJson("/status")
        .then((h) => {
          if (!cancelled) setHealth({ ok: h.status === "running" });
        })
        .catch(() => {
          if (!cancelled) setHealth({ ok: false });
        });
    }
    ping();
    const t = setInterval(ping, 30000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  const ok = health.ok === true;
  const color = ok ? "#4AF626" : health.ok === false ? "#EF4444" : "#5A5A5A";
  const text = ok ? "ONLINE" : health.ok === false ? "OFFLINE" : "SYNC…";
  return (
    <div className="flex items-center gap-2 border border-edge bg-surface px-2.5 py-1">
      <span
        className={ok ? "phosphor-dot" : "inline-block h-1.5 w-1.5"}
        style={!ok ? { backgroundColor: color } : undefined}
      />
      <span
        className="font-mono uppercase tracking-telemetry text-[10px] font-semibold"
        style={{ color }}
      >
        {text}
      </span>
    </div>
  );
}

function Clock() {
  const [stamp, setStamp] = useState(nowStamp());
  useEffect(() => {
    const t = setInterval(() => setStamp(nowStamp()), 1000);
    return () => clearInterval(t);
  }, []);
  return (
    <span className="font-mono text-[10px] tracking-telemetry text-fg-muted">
      {stamp}
    </span>
  );
}

export function Sidebar() {
  return (
    <aside className="hidden md:flex md:flex-col md:w-[220px] md:shrink-0 border-r border-edge bg-canvas">
      <Brand />
      <nav className="flex-1 py-2">
        <NavItem to="/" end glyph=">" label="DASHBOARD" />
        <NavItem to="/work-items" glyph="#" label="WORK ITEMS" />
        <NavItem to="/apps" glyph="◉" label="APPS" />
        <NavItem to="/settings" glyph="⚙" label="SETTINGS" />
        <NavItem to="/model-hosts" glyph="◈" label="MODEL HOSTS" />
      </nav>
      <div className="border-t border-edge px-4 py-3 space-y-1.5">
        <div className="label-tel">UNIT / D-01</div>
        <div className="label-tel">REV 0.1.0</div>
      </div>
    </aside>
  );
}

export function MobileNav() {
  return (
    <div className="md:hidden flex items-center gap-1 border-b border-edge bg-canvas px-3 py-2 overflow-x-auto">
      <NavLink
        to="/"
        end
        className={({ isActive }) =>
          `px-3 py-1.5 border font-mono uppercase tracking-telemetry text-[11px] font-semibold ${
            isActive
              ? "border-fg-primary text-fg-primary bg-raised"
              : "border-edge text-fg-secondary"
          }`
        }
      >
        DASHBOARD
      </NavLink>
      <NavLink
        to="/work-items"
        className={({ isActive }) =>
          `px-3 py-1.5 border font-mono uppercase tracking-telemetry text-[11px] font-semibold ${
            isActive
              ? "border-fg-primary text-fg-primary bg-raised"
              : "border-edge text-fg-secondary"
          }`
        }
      >
        WORK ITEMS
      </NavLink>
      <NavLink
        to="/apps"
        className={({ isActive }) =>
          `px-3 py-1.5 border font-mono uppercase tracking-telemetry text-[11px] font-semibold ${
            isActive
              ? "border-fg-primary text-fg-primary bg-raised"
              : "border-edge text-fg-secondary"
          }`
        }
      >
        APPS
      </NavLink>
      <NavLink
        to="/settings"
        className={({ isActive }) =>
          `px-3 py-1.5 border font-mono uppercase tracking-telemetry text-[11px] font-semibold ${
            isActive
              ? "border-fg-primary text-fg-primary bg-raised"
              : "border-edge text-fg-secondary"
          }`
        }
      >
        SETTINGS
      </NavLink>
      <NavLink
        to="/model-hosts"
        className={({ isActive }) =>
          `px-3 py-1.5 border font-mono uppercase tracking-telemetry text-[11px] font-semibold ${
            isActive
              ? "border-fg-primary text-fg-primary bg-raised"
              : "border-edge text-fg-secondary"
          }`
        }
      >
        MODEL HOSTS
      </NavLink>
    </div>
  );
}

export function TopBar({ crumbs = [] }) {
  return (
    <header className="flex items-center justify-between gap-3 border-b border-edge bg-canvas px-4 md:px-6 py-2.5">
      <div className="flex items-center gap-2 min-w-0 overflow-hidden">
        <span className="md:hidden font-display font-extrabold tracking-tighter-display text-fg-primary text-lg">
          LEGION
        </span>
        <span className="hidden md:inline font-mono text-[10px] tracking-telemetry text-fg-muted">
          [
        </span>
        <ol className="hidden md:flex items-center gap-2 min-w-0 overflow-hidden">
          {crumbs.map((c, i) => (
            <li key={i} className="flex items-center gap-2 min-w-0">
              {i > 0 && (
                <span className="font-mono text-fg-muted text-xs">/</span>
              )}
              <span
                className={`font-mono uppercase tracking-telemetry text-[11px] truncate ${
                  i === crumbs.length - 1 ? "text-fg-primary" : "text-fg-secondary"
                }`}
              >
                {c}
              </span>
            </li>
          ))}
        </ol>
        <span className="hidden md:inline font-mono text-[10px] tracking-telemetry text-fg-muted">
          ]
        </span>
      </div>
      <div className="flex items-center gap-3 shrink-0">
        <Clock />
        <HealthIndicator />
      </div>
    </header>
  );
}
