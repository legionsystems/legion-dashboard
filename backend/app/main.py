import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .apps_config import APPS_CONFIG
from .config import settings
from .database import Base, SessionLocal, engine
from .models import App
from .routers import apps, model_hosts as model_hosts_router, settings as settings_router, stats, work_items
from .routers import debate_cleanup
from .routers import builder as builder_router
from .routers import repo_safety as repo_safety_router

Base.metadata.create_all(bind=engine)


def seed_apps() -> None:
    """Upsert configured apps into the DB so the apps router can address them.

    Runtime state (status, last_action) is preserved across restarts; only the
    static fields (name, repo, compose_project, compose_path) are reconciled.
    """
    db = SessionLocal()
    try:
        existing = {a.app_id: a for a in db.query(App).all()}
        for defn in APPS_CONFIG:
            row = existing.get(defn.app_id)
            if row is None:
                db.add(
                    App(
                        app_id=defn.app_id,
                        name=defn.name,
                        repo=defn.repo,
                        compose_project=defn.compose_project,
                        compose_path=defn.compose_path,
                    )
                )
            else:
                row.name = defn.name
                row.repo = defn.repo
                row.compose_project = defn.compose_project
                row.compose_path = defn.compose_path
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Tests set LEGION_SKIP_SEED=1 to keep the apps table empty between fixtures.
    if os.environ.get("LEGION_SKIP_SEED") != "1":
        seed_apps()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.include_router(work_items.router)
app.include_router(stats.router)
app.include_router(apps.router)
app.include_router(settings_router.router)
app.include_router(model_hosts_router.router)
app.include_router(debate_cleanup.router)
app.include_router(builder_router.router)
app.include_router(repo_safety_router.router)

# Serve built frontend SPA from /app/frontend/dist in container
# In dev, this path may not exist; in Docker, it's copied from the build stage
_FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    # Mount static assets (JS/CSS chunks from Vite build)
    app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")


@app.get("/api/status")
def api_status():
    return {
        "app_id": settings.app_id,
        "status": "running",
        "version": settings.app_version,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/version")
def version():
    return {
        "app_id": settings.app_id,
        "app_name": settings.app_name,
        "app_version": settings.app_version,
    }


@app.get("/federation/manifest")
def federation_manifest():
    return {
        "app_id": settings.app_id,
        "app_name": settings.app_name,
        "app_version": settings.app_version,
        "endpoints": {
            "health": "/health",
            "version": "/version",
            "status": "/api/status",
            "work_items": "/api/work-items",
            "stats": "/api/stats",
            "apps": "/api/apps",
        },
    }


# SPA serving - must be LAST so API routes take precedence
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_spa_root():
    """Serve the SPA index.html at root."""
    index_html = _FRONTEND_DIST / "index.html"
    if index_html.exists():
        return HTMLResponse(content=index_html.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>LEGION Dashboard</h1><p>Frontend not built. Run `npm run build` in frontend/</p>",
        status_code=200,
    )


@app.get("/{full_path:path}", response_class=HTMLResponse, include_in_schema=False)
async def spa_fallback(full_path: str):
    """Serve the SPA index.html for any non-API route (client-side routing)."""
    # Don't intercept API routes or static assets
    if full_path.startswith("api/") or full_path.startswith("assets/"):
        return HTMLResponse(content="<h1>Not Found</h1>", status_code=404)
    index_html = _FRONTEND_DIST / "index.html"
    if index_html.exists():
        return HTMLResponse(content=index_html.read_text(encoding="utf-8"))
    return HTMLResponse(
        content="<h1>LEGION Dashboard</h1><p>Frontend not built. Run `npm run build` in frontend/</p>",
        status_code=200,
    )
