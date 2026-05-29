import { Link } from "react-router-dom";
import { INTAKE_TYPES, TypeBadge, typeMeta } from "../components/badges.jsx";
import { Panel } from "../components/panel.jsx";

const TYPE_DESCRIPTIONS = {
  idea: "Speculative direction. Capture before it evaporates.",
  bug: "Observed defect. Reproducible failure.",
  change: "Modification to existing behavior or scope.",
  task: "Discrete unit of work with clear definition of done.",
  slice: "Vertical cut of user value across the stack.",
};

function TypeCard({ type }) {
  const meta = typeMeta(type);
  return (
    <Link
      to={`/work-items/new/${type}`}
      className="block border border-edge bg-surface hover:bg-raised transition-colors group"
    >
      <div className="border-l-4 px-4 py-4" style={{ borderColor: meta.color }}>
        <div className="flex items-center justify-between">
          <TypeBadge type={type} />
          <span
            className="font-mono text-[11px] uppercase tracking-telemetry text-fg-muted group-hover:text-fg-primary"
          >
            FILE &gt;
          </span>
        </div>
        <div className="mt-3 font-display text-lg font-bold tracking-tight">
          NEW {type.toUpperCase()}
        </div>
        <p className="mt-1 text-sm text-fg-secondary">
          {TYPE_DESCRIPTIONS[type]}
        </p>
      </div>
    </Link>
  );
}

export default function WorkItemNew() {
  return (
    <div className="space-y-5 max-w-4xl">
      <div>
        <Link
          to="/work-items"
          className="font-mono uppercase tracking-telemetry text-[11px] text-fg-secondary hover:text-fg-primary"
        >
          ← BACK TO QUEUE
        </Link>
      </div>

      <div>
        <div className="label-tel">INTAKE / NEW</div>
        <h1
          className="font-display font-extrabold tracking-tighter-display leading-none mt-1"
          style={{ fontSize: "clamp(2rem, 5vw, 3rem)" }}
        >
          SELECT TYPE
        </h1>
        <p className="mt-2 text-sm text-fg-secondary max-w-xl">
          Choose the kind of work being filed. Type drives the form, the
          downstream pipeline, and the badge.
        </p>
      </div>

      <Panel title="INTAKE TYPES" subtitle="// pick one">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-px bg-edge -mx-4 -my-3 border-y border-edge">
          {INTAKE_TYPES.map((t) => (
            <TypeCard key={t} type={t} />
          ))}
        </div>
      </Panel>
    </div>
  );
}
