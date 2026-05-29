# LEGION Dashboard

Control-plane dashboard for the LEGION work-item pipeline. A dark, high-density operator console for inspecting, triaging, approving, and blocking AI builder/reviewer work items.

```
[ LEGION® / CONTROL PLANE / v0.1 ]
```

## What's in here

```
legion-dashboard/
├── backend/        FastAPI + SQLAlchemy + Alembic
├── frontend/       React + Vite + Tailwind (dark tactical-telemetry UI)
├── scripts/        Local helper scripts
├── docker-compose.yml
└── Dockerfile
```

### Backend (`backend/`)

FastAPI service exposing the work-item registry.

| Endpoint | Description |
| --- | --- |
| `GET /health` | Liveness probe |
| `GET /version` | App id / name / version |
| `GET /federation/manifest` | Hub-facing manifest of endpoints |
| `GET /api/status` | Runtime status |
| `GET /api/stats` | Aggregated counts (by status, by type), awaiting-approval count, recent activity |
| `GET /api/work-items` | List work items (filter by `type`, `status`) |
| `POST /api/work-items` | Create work item |
| `GET /api/work-items/{id}` | Fetch single work item |
| `PUT /api/work-items/{id}` | Update work item |
| `POST /api/work-items/{id}/approve` | Operator approval (timestamps the action) |
| `POST /api/work-items/{id}/block` | Block with optional `override_reason` |
| `GET /api/work-items/{id}/follow-ups` | List reviewer follow-ups |
| `POST /api/work-items/{id}/follow-ups` | Create reviewer follow-up |

Models: `WorkItem` (lifecycle status, builder/reviewer provenance, approval/override stamps, PR/merge metadata) and `FollowUp` (severity, body, status).

### Frontend (`frontend/`)

React 18 + Vite + Tailwind. UI direction is **tactical telemetry**: dark CRT canvas (`#0A0A0A`), JetBrains Mono telemetry labels, Inter display headings, rectangular borders (no radius), high-contrast color-coded status badges, and a left-rail sidebar with branded header.

Pages:

- `/` — **Dashboard.** Summary metrics (total / awaiting / in-flight / blocked), status distribution bars, type breakdown, recent activity feed.
- `/work-items` — **Work Item Registry.** Status filter pills (with counts), type filter, free-text search, dense card/table hybrid with status & type badges.
- `/work-items/new` and `/work-items/:id/edit` — **Form.** Type selector (badge picker), status select with preview, title (required, validated), body, hint counts, clear submit/cancel.
- `/work-items/:id` — **Detail.** Status + type badges, created/updated/approved timestamps, body, provenance panel (builder & reviewer fields), delivery panel (PR / merge commit / overrides), block/override textarea, follow-ups list.

Empty, loading (skeleton shimmer), and error states are first-class throughout.

## Local development

### Backend

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -e .[dev]
.venv/bin/uvicorn app.main:app --reload --port 8080
.venv/bin/pytest tests/ -v
```

By default the backend uses the `DATABASE_URL` env var. For local dev without Postgres, set `DATABASE_URL=sqlite:///./test.db` (the test suite does this automatically).

### Frontend

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173, proxies /api → 127.0.0.1:8080
npm run build
```

### Docker (full stack)

```bash
cp .env.example .env
docker compose up --build
# App → http://localhost:8720
```

## Design language

The frontend is a deliberate departure from generic AI dashboards. Visual rules:

- Dark canvas (`#0A0A0A`), white phosphor text (`#EAEAEA`), aviation red (`#E61919`) for alerts, terminal phosphor (`#4AF626`) for the single live status dot.
- All metadata labels: JetBrains Mono, uppercase, `0.08em` tracking.
- Status badges carry the exact spec colors (draft=gray, debated=purple, approved=blue, active=amber, review_needed=orange, certified=teal, pr_open=indigo, ready_for_merge=cyan, merged=green, blocked=red, completed=emerald).
- No `border-radius`. Visible 1px borders compartmentalize panels. CSS grid `gap: 1px` produces razor dividers between metric cards.
- Subtle CRT scanline overlay reserved for ambient framing (not applied globally to keep text crisp).

## Status

Scaffold. CRUD + approval/block actions wired end-to-end; GitHub mutations, deployment, and authentication intentionally out of scope.
