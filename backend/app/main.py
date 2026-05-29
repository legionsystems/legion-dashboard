from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import Base, engine
from .routers import stats, work_items

Base.metadata.create_all(bind=engine)

app = FastAPI(title=settings.app_name, version=settings.app_version)

app.include_router(work_items.router)
app.include_router(stats.router)

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
